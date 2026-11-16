/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * The Discord IPC protocol, shared by the Mac and Windows hosts.
 *
 * Opcodes are little-endian uint32 pairs (opcode, length) in front of a JSON
 * payload: HANDSHAKE 0 with {"v":1,"client_id":...}, FRAME 1 for commands and
 * replies, CLOSE 2. SET_ACTIVITY carries the activity; Discord echoes it back
 * in its reply, which is the proof a test reads in the log.
 *
 * The activity is sent once per connection. Discord counts elapsed time from
 * the start timestamp, and the client may only update its status five times in
 * twenty seconds, so there is nothing to gain by sending it on every tick.
 * Stage changes happen only on the worker thread; the setting and the stop
 * request are atomics the host writes.
 */
#include "pvz2-discord.h"

#include <pthread.h>
#include <stdbool.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define TICK_MS 200
#define RETRY_TICKS 10                  /* 2 s between connects */
#define QUIET_TICKS 150                 /* 30 s between "Discord is not running" notes */

typedef enum {
    ST_OFFLINE,
    ST_HANDSHAKE,
    ST_ACTIVITY,
    ST_LIVE
} discord_stage;

typedef struct {
    discord_stage stage;
    tl_discord_conn conn;
    unsigned char rx[8192];
    int rx_len;
    char client_id[24];
    char activity[1024];
    char clear[256];
    unsigned long long start_ms;
    int pid;
    tl_discord_log_fn log;
    unsigned ticks, retry_at, note_at;
} discord_state;

static discord_state g;
static atomic_bool g_enabled;
static atomic_bool g_stop;
static atomic_bool g_stopped = 1;

static void put32(unsigned char *p, unsigned v)
{
    p[0] = (unsigned char)v; p[1] = (unsigned char)(v >> 8);
    p[2] = (unsigned char)(v >> 16); p[3] = (unsigned char)(v >> 24);
}

static unsigned get32(const unsigned char *p)
{
    return (unsigned)p[0] | ((unsigned)p[1] << 8) | ((unsigned)p[2] << 16) |
           ((unsigned)p[3] << 24);
}

static int send_frame(discord_state *s, unsigned opcode, const char *json, int len)
{
    unsigned char frame[2048];
    if (len < 0 || len + 8 > (int)sizeof(frame)) return -1;
    put32(frame, opcode);
    put32(frame + 4, (unsigned)len);
    if (len) memcpy(frame + 8, json, (size_t)len);
    return tl_discord_transport_write(&s->conn, frame, len + 8);
}

static void set_client_id(discord_state *s, const char *override, tl_discord_log_fn log)
{
    const char *id = (override && *override) ? override : TL_DISCORD_APPLICATION_ID;
    if (override && *override) {
        size_t n = strlen(id);
        if (n > 20) n = 20;
        for (size_t i = 0; i < n; i++) {
            if (id[i] < '0' || id[i] > '9') {
                if (log) log("discord: the launch override is not an application id; using the one in pvz2-discord.h");
                id = TL_DISCORD_APPLICATION_ID;
                break;
            }
        }
    }
    snprintf(s->client_id, sizeof(s->client_id), "%s", id);
}

static void build_payloads(discord_state *s)
{
    /* A snowflake is at most 20 digits; the activity's URLs and asset key are
     * fixed constants, so no JSON escaping is needed here. */
    snprintf(s->activity, sizeof(s->activity),
             "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d,\"activity\":"
             "{\"name\":\"%s\",\"type\":0,\"timestamps\":{\"start\":%llu},"
             "\"assets\":{\"large_image\":\"%s\"},"
             "\"buttons\":[{\"label\":\"%s\",\"url\":\"%s\"}]}},"
             "\"nonce\":\"pvz2-activity\"}",
             s->pid, TL_DISCORD_APP_NAME, s->start_ms, TL_DISCORD_LARGE_IMAGE,
             TL_DISCORD_BUTTON_LABEL, TL_DISCORD_BUTTON_URL);
    snprintf(s->clear, sizeof(s->clear),
             "{\"cmd\":\"SET_ACTIVITY\",\"args\":{\"pid\":%d,\"activity\":null},"
             "\"nonce\":\"pvz2-clear\"}", s->pid);
}

static int has(const char *json, const char *field)
{
    return strstr(json, field) != NULL;
}

static void log_error(discord_state *s, const char *json)
{
    int code = 0;
    const char *p = strstr(json, "\"code\":");
    if (p) code = (int)strtol(p + 7, NULL, 10);
    char msg[200] = "";
    const char *q = strstr(json, "\"message\":");
    if (q) {
        q += 10;
        while (*q == ' ') q++;
        if (*q == '"') {
            int i = 0;
            for (q++; *q && *q != '"' && i < (int)sizeof(msg) - 1; i++) msg[i] = *q++;
            msg[i] = 0;
        }
    }
    s->log("discord: error %d: %s", code, msg[0] ? msg : "(no message)");
}

static void log_reply(discord_state *s, const char *what, const char *json, int len)
{
    char buf[900];
    if (len > (int)sizeof(buf) - 1) len = (int)sizeof(buf) - 1;
    memcpy(buf, json, (size_t)len);
    buf[len] = 0;
    s->log("discord: %s: %s", what, buf);
}

static void disconnect(discord_state *s, int clear)
{
    if (s->conn.handle) {
        if (clear) {
            send_frame(s, 1, s->clear, (int)strlen(s->clear));
            send_frame(s, 2, "", 0);
            s->log("discord: the activity is cleared and the connection is closed");
        }
        tl_discord_transport_close(&s->conn);
        s->conn.handle = NULL;
    }
    s->rx_len = 0;
    s->stage = ST_OFFLINE;
    s->retry_at = s->ticks + RETRY_TICKS;
}

static void handle_frame(discord_state *s, unsigned opcode, const unsigned char *payload, int len)
{
    char json[4200];
    if (len > (int)sizeof(json) - 1) len = (int)sizeof(json) - 1;
    memcpy(json, payload, (size_t)len);
    json[len] = 0;
    int error = has(json, "\"evt\":\"ERROR\"") || has(json, "\"evt\": \"ERROR\"");

    if (opcode == 2) {
        /* Discord answers a bad handshake with CLOSE, not FRAME: the error code
         * and message are in this payload, so they have to be read here too. */
        if (len > 2) log_reply(s, "close reply", json, len);
        if (strstr(json, "\"code\":")) log_error(s, json);
        s->log("discord: the client closed the connection");
        disconnect(s, 0);
        return;
    }
    if (opcode != 1) return;

    if (s->stage == ST_HANDSHAKE) {
        log_reply(s, "handshake reply", json, len);
        if (has(json, "\"evt\":\"READY\"") || has(json, "\"evt\": \"READY\"")) {
            if (send_frame(s, 1, s->activity, (int)strlen(s->activity)) == 0) {
                s->log("discord: SET_ACTIVITY sent (start %llu, large image %s, one button)",
                       s->start_ms, TL_DISCORD_LARGE_IMAGE);
                s->stage = ST_ACTIVITY;
            } else {
                s->log("discord: SET_ACTIVITY could not be written");
                disconnect(s, 0);
            }
        } else if (error) {
            log_error(s, json);
            disconnect(s, 0);
        }
        return;
    }
    if (s->stage == ST_ACTIVITY) {
        log_reply(s, "SET_ACTIVITY reply", json, len);
        if (error) log_error(s, json);
        s->stage = ST_LIVE;
    }
}

static void pump(discord_state *s)
{
    if (!atomic_load_explicit(&g_enabled, memory_order_relaxed)) {
        if (s->stage != ST_OFFLINE) {
            s->log("discord: the setting is off: nothing is sent");
            disconnect(s, 1);
        }
        return;
    }
    if (s->stage == ST_OFFLINE) {
        if (s->ticks < s->retry_at) return;
        if (tl_discord_transport_open(&s->conn) != 0) {
            s->retry_at = s->ticks + RETRY_TICKS;
            if (s->ticks >= s->note_at) {
                s->log("discord: not running; trying again every 2 s (silent from now on)");
                s->note_at = s->ticks + QUIET_TICKS;
            }
            return;
        }
        s->rx_len = 0;
        char handshake[128];
        snprintf(handshake, sizeof(handshake), "{\"v\":1,\"client_id\":\"%s\"}", s->client_id);
        s->log("discord: connected to discord-ipc-%d, handshake v1, client id %s",
               s->conn.index, *s->client_id ? s->client_id : "(empty)");
        int written = (int)strlen(handshake);
        if (send_frame(s, 0, handshake, written) != 0) {
            s->log("discord: the handshake could not be written");
            disconnect(s, 0);
            return;
        }
        s->log("discord: handshake frame written (%d bytes)", written + 8);
        s->stage = ST_HANDSHAKE;
        return;
    }
    if (s->stage == ST_LIVE) return;

    int n = tl_discord_transport_read(&s->conn, s->rx + s->rx_len,
                                      (int)sizeof(s->rx) - s->rx_len);
    if (n < 0) {
        s->log("discord: the connection ended");
        disconnect(s, 0);
        return;
    }
    s->rx_len += n;
    while (s->rx_len >= 8) {
        unsigned opcode = get32(s->rx), len = get32(s->rx + 4);
        if (len > 4096) {
            s->log("discord: a frame was too large (%u); trying again", len);
            disconnect(s, 0);
            return;
        }
        if (s->rx_len < 8 + (int)len) break;
        handle_frame(s, opcode, s->rx + 8, (int)len);
        int used = 8 + (int)len;
        memmove(s->rx, s->rx + used, (size_t)(s->rx_len - used));
        s->rx_len -= used;
        if (s->stage == ST_OFFLINE || s->stage == ST_LIVE) break;
    }
}

static void *worker(void *unused)
{
    (void)unused;
    while (!atomic_load_explicit(&g_stop, memory_order_relaxed)) {
        pump(&g);
        g.ticks++;
        tl_discord_sleep_ms(TICK_MS);
    }
    if (g.stage != ST_OFFLINE) disconnect(&g, 1);
    atomic_store_explicit(&g_stopped, true, memory_order_relaxed);
    return NULL;
}

void tl_discord_start(unsigned long long start_unix_ms, const char *app_id_override,
                      int enabled, tl_discord_log_fn log)
{
    memset(&g, 0, sizeof(g));
    g.log = log;
    g.start_ms = start_unix_ms;
    g.pid = tl_discord_process_id();
    set_client_id(&g, app_id_override, log);
    build_payloads(&g);
    /* Set before the thread exists: with the setting off, not even a connect
     * may slip out before the host's first update reaches the worker. */
    atomic_store_explicit(&g_enabled, enabled != 0, memory_order_relaxed);
    atomic_store_explicit(&g_stop, false, memory_order_relaxed);
    atomic_store_explicit(&g_stopped, false, memory_order_relaxed);
    if (log) log("discord: rich presence started (application id %s)",
                 *g.client_id ? g.client_id : "(empty, fill in pvz2-discord.h)");
    pthread_t thread;
    if (pthread_create(&thread, NULL, worker, NULL) != 0) {
        atomic_store_explicit(&g_stopped, true, memory_order_relaxed);
        if (log) log("discord: the worker thread could not start");
        return;
    }
    pthread_detach(thread);
}

void tl_discord_set_enabled(int enabled)
{
    atomic_store_explicit(&g_enabled, enabled != 0, memory_order_relaxed);
}

void tl_discord_stop(void)
{
    atomic_store_explicit(&g_stop, true, memory_order_relaxed);
    for (int i = 0; i < 20 && !atomic_load_explicit(&g_stopped, memory_order_relaxed); i++)
        tl_discord_sleep_ms(20);
}
