/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * The Windows transport for Discord's local RPC: a named pipe at
 * \\?\pipe\discord-ipc-0..9. The pipe is opened in byte, non-blocking mode so
 * a read with no reply returns at once and the worker can notice the setting
 * change or the stop request within one tick.
 */
#define _WIN32_WINNT 0x0A00
#define WINVER 0x0A00
#include "pvz2-discord.h"

#include <windows.h>
#include <stdio.h>

int tl_discord_transport_open(tl_discord_conn *conn)
{
    for (int i = 0; i < 10; i++) {
        wchar_t name[64];
        swprintf(name, sizeof(name) / sizeof(name[0]), L"\\\\?\\pipe\\discord-ipc-%d", i);
        HANDLE h = CreateFileW(name, GENERIC_READ | GENERIC_WRITE, 0, NULL,
                               OPEN_EXISTING, 0, NULL);
        if (h == INVALID_HANDLE_VALUE) continue;
        DWORD mode = PIPE_READMODE_BYTE | PIPE_NOWAIT;
        if (!SetNamedPipeHandleState(h, &mode, NULL, NULL)) {
            CloseHandle(h);
            continue;
        }
        conn->handle = h;
        conn->index = i;
        return 0;
    }
    return -1;
}

int tl_discord_transport_read(tl_discord_conn *conn, unsigned char *buf, int cap)
{
    DWORD got = 0;
    if (ReadFile((HANDLE)conn->handle, buf, (DWORD)cap, &got, NULL)) return got > 0 ? (int)got : -1;
    DWORD err = GetLastError();
    if (err == ERROR_NO_DATA) return 0;                 /* nothing ready yet */
    return -1;
}

int tl_discord_transport_write(tl_discord_conn *conn, const unsigned char *buf, int len)
{
    const unsigned char *p = buf;
    int left = len;
    for (int tries = 0; left > 0 && tries < 20; tries++) {
        DWORD put = 0;
        if (WriteFile((HANDLE)conn->handle, p, (DWORD)left, &put, NULL)) {
            p += put;
            left -= (int)put;
            continue;
        }
        DWORD err = GetLastError();
        if (err == ERROR_NO_DATA || err == ERROR_PIPE_BUSY) {
            Sleep(1);
            continue;
        }
        return -1;
    }
    return left ? -1 : 0;
}

void tl_discord_transport_close(tl_discord_conn *conn)
{
    if (conn->handle) CloseHandle((HANDLE)conn->handle);
    conn->handle = NULL;
}

int tl_discord_process_id(void)
{
    return (int)GetCurrentProcessId();
}

void tl_discord_sleep_ms(int ms)
{
    Sleep((DWORD)ms);
}
