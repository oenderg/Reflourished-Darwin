/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * Discord Rich Presence over the local RPC socket or named pipe.
 *
 * The protocol and the state machine live in pvz2-discord.c; the two transport
 * files only open, read, write and close a byte stream. The host starts one
 * worker thread and tells it when the setting changes, so nothing here runs on
 * the game's render thread. Discord is optional: a failed connect is retried
 * quietly, and a disconnect clears the activity without disturbing the game.
 */
#ifndef PVZ2_DISCORD_H
#define PVZ2_DISCORD_H

/* The one place the Discord application id lives (the "PvZ2: Reflourished"
 * application in Discord's developer portal). TL_DISCORD_APP_ID or
 * --discord-app-id=<id> overrides it at launch for a test. */
#define TL_DISCORD_APPLICATION_ID "1571926819857244202"
#define TL_DISCORD_APP_NAME "PvZ2: Reflourished"
#define TL_DISCORD_BUTTON_LABEL TL_DISCORD_APP_NAME
#define TL_DISCORD_BUTTON_URL "https://pvz.oender.dev/#reflourished"
/* The app icon by its web address: the repo's own icon on the default branch,
 * so Discord shows the current one and no art is uploaded to the application. */
#define TL_DISCORD_LARGE_IMAGE "https://raw.githubusercontent.com/oenderg/Reflourished-Darwin/HEAD/docs/banner/icon.png"

typedef void (*tl_discord_log_fn)(const char *fmt, ...);

/* The platform's byte stream. The protocol never looks inside it. */
typedef struct {
    void *handle;   /* a file descriptor on the Mac, a HANDLE on Windows */
    int index;      /* which discord-ipc-0..9 answered */
} tl_discord_conn;

/* Implemented once per platform in pvz2-discord-{macos,windows}.c. Read returns
 * more than zero bytes, zero when none are ready right now, and less than zero
 * on EOF or error. Write returns zero on success and less than zero on error. */
int  tl_discord_transport_open(tl_discord_conn *conn);
int  tl_discord_transport_read(tl_discord_conn *conn, unsigned char *buf, int cap);
int  tl_discord_transport_write(tl_discord_conn *conn, const unsigned char *buf, int len);
void tl_discord_transport_close(tl_discord_conn *conn);
int  tl_discord_process_id(void);
void tl_discord_sleep_ms(int ms);

/* The host's side. start is called once; the timestamp is Unix milliseconds
 * and is when the game started, so Discord shows elapsed time by itself. */
void tl_discord_start(unsigned long long start_unix_ms, const char *app_id_override,
                      int enabled, tl_discord_log_fn log);
void tl_discord_set_enabled(int enabled);
/* Bounded: clears the activity, closes the stream and waits at most 400 ms for
 * the worker to notice. The worker is detached, so nothing joins a stuck thread. */
void tl_discord_stop(void);

#endif /* PVZ2_DISCORD_H */
