/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * The Mac transport for Discord's local RPC: a Unix domain socket at
 * $XDG_RUNTIME_DIR/discord-ipc-0..9, $TMPDIR, $TMP, $TEMP, then /tmp. The
 * prefix order and the socket range are the ones Discord's RPC docs give.
 * Reads are non-blocking; writes are short and local, so they cannot hold up
 * the worker (and never the game) for more than a moment.
 */
#include "pvz2-discord.h"

#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <unistd.h>

int tl_discord_transport_open(tl_discord_conn *conn)
{
    const char *base = NULL;
    const char *env = getenv("XDG_RUNTIME_DIR");
    if (env && *env) base = env;
    if (!base) { env = getenv("TMPDIR"); if (env && *env) base = env; }
    if (!base) { env = getenv("TMP"); if (env && *env) base = env; }
    if (!base) { env = getenv("TEMP"); if (env && *env) base = env; }
    if (!base) base = "/tmp";

    for (int i = 0; i < 10; i++) {
        char path[256];
        snprintf(path, sizeof(path), "%s/discord-ipc-%d", base, i);
        struct sockaddr_un addr;
        memset(&addr, 0, sizeof(addr));
        addr.sun_family = AF_UNIX;
        snprintf(addr.sun_path, sizeof(addr.sun_path), "%s", path);
        int fd = socket(AF_UNIX, SOCK_STREAM, 0);
        if (fd < 0) return -1;
        /* A write to a socket Discord has closed must fail with EPIPE, not
         * raise SIGPIPE, which would end the game. */
        int one = 1;
        setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &one, sizeof(one));
        if (connect(fd, (struct sockaddr *)&addr, sizeof(addr)) == 0) {
            conn->handle = (void *)(intptr_t)fd;
            conn->index = i;
            return 0;
        }
        close(fd);
    }
    return -1;
}

int tl_discord_transport_read(tl_discord_conn *conn, unsigned char *buf, int cap)
{
    if (cap <= 0) return 0;
    ssize_t n = recv((int)(intptr_t)conn->handle, buf, (size_t)cap, MSG_DONTWAIT);
    if (n > 0) return (int)n;
    if (n == 0) return -1;                              /* EOF */
    if (errno == EAGAIN || errno == EWOULDBLOCK) return 0; /* nothing ready */
    return -1;
}

int tl_discord_transport_write(tl_discord_conn *conn, const unsigned char *buf, int len)
{
    const unsigned char *p = buf;
    int left = len;
    while (left > 0) {
        ssize_t n = send((int)(intptr_t)conn->handle, p, (size_t)left, 0);
        if (n > 0) { p += n; left -= (int)n; continue; }
        if (n < 0 && (errno == EAGAIN || errno == EINTR)) continue;
        return -1;
    }
    return 0;
}

void tl_discord_transport_close(tl_discord_conn *conn)
{
    if (conn->handle) close((int)(intptr_t)conn->handle);
    conn->handle = NULL;
}

int tl_discord_process_id(void)
{
    return (int)getpid();
}

void tl_discord_sleep_ms(int ms)
{
    usleep((unsigned)ms * 1000);
}
