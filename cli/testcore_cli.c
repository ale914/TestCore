// Copyright (c) 2026 Alessandro Ricco
// Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
// See LICENSE file for details.

/* TestCore CLI - Interactive client for TestCore server */

#include <winsock2.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <stdarg.h>
#include <ctype.h>
#include <conio.h>
#include <windows.h>
#include "linenoise/linenoise.h"

#pragma comment(lib, "ws2_32.lib")

#define BUF_SIZE      1024
#define RECV_SIZE     65536
#define DEFAULT_HOST  "127.0.0.1"
#define DEFAULT_PORT  6399
#define RECONNECT_MS  2000

/* ANSI codes */
#define C_RST  "\x1b[0m"
#define C_RED  "\x1b[31m"
#define C_GRN  "\x1b[32m"
#define C_YEL  "\x1b[33m"
#define C_CYN  "\x1b[36m"
#define C_GRY  "\x1b[90m"

/* -- Output buffer: batch all output into one WriteConsoleA call -- */

static char *g_buf;
static int g_pos, g_cap;

static void ob_start(int cap) {
    g_buf = (char *)malloc(cap);
    g_pos = 0;
    g_cap = cap;
}

static void ob_end(void) {
    if (g_buf && g_pos > 0) {
        DWORD w;
        WriteConsoleA(GetStdHandle(STD_OUTPUT_HANDLE), g_buf, g_pos, &w, NULL);
    }
    free(g_buf);
    g_buf = NULL;
    g_pos = g_cap = 0;
}

static void ob_write(const char *s, int n) {
    if (g_pos + n < g_cap) { memcpy(g_buf + g_pos, s, n); g_pos += n; }
}

#ifdef __GNUC__
__attribute__((format(printf, 1, 2)))
#endif
static void out(const char *fmt, ...) {
    va_list a;
    va_start(a, fmt);
    if (g_buf) { int n = vsnprintf(g_buf + g_pos, g_cap - g_pos, fmt, a); if (n > 0) g_pos += n; }
    else vprintf(fmt, a);
    va_end(a);
}

static void cset(const char *c) {
    if (g_buf) ob_write(c, (int)strlen(c));
    else fputs(c, stdout);
}

static void enable_vt(void) {
    HANDLE h = GetStdHandle(STD_OUTPUT_HANDLE);
    DWORD m;
    if (GetConsoleMode(h, &m)) SetConsoleMode(h, m | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
}

/* -- Command cache for TAB completion -- */

#define CACHE_MAX 128
static char *cmd_cache[CACHE_MAX];
static int cmd_count;

static void cache_add(const char *s) {
    if (cmd_count < CACHE_MAX) cmd_cache[cmd_count++] = _strdup(s);
}

static void cache_clear(void) {
    for (int i = 0; i < cmd_count; i++) free(cmd_cache[i]);
    cmd_count = 0;
}

static void cache_fetch(SOCKET s, char *buf, int sz) {
    cache_clear();
    cache_add("CLEAR");
    cache_add("EXIT");

    const char *q = "COMMAND LIST\r\n";
    if (send(s, q, (int)strlen(q), 0) == SOCKET_ERROR) return;

    int total = 0, n;
    n = recv(s, buf, sz - 1, 0);
    if (n <= 0) return;
    total = n;

    int tmo = 50;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tmo, sizeof(tmo));
    while (total < sz - 1) {
        n = recv(s, buf + total, sz - 1 - total, 0);
        if (n <= 0) break;
        total += n;
    }
    tmo = 0;
    setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, (const char *)&tmo, sizeof(tmo));
    buf[total] = '\0';

    if (total <= 0 || buf[0] != '*') return;

    /* Parse RESP array */
    int p = 1;
    while (p < total && buf[p] != '\r') p++;
    int cnt = atoi(buf + 1);
    if (p < total) p += 2;

    for (int i = 0; i < cnt && p < total; i++) {
        if (buf[p] == '$') {
            p++;
            int st = p;
            while (p < total && buf[p] != '\r') p++;
            int slen = atoi(buf + st);
            if (p < total) p += 2;
            if (slen > 0 && p + slen <= total) {
                char cmd[256];
                if (slen < (int)sizeof(cmd)) {
                    memcpy(cmd, buf + p, slen);
                    cmd[slen] = '\0';
                    cache_add(cmd);
                }
                p += slen;
                if (p < total) p += 2;
            }
        }
    }
}

/* -- Linenoise callbacks -- */

static void completion_cb(const char *buf, linenoiseCompletions *lc, void *ud) {
    (void)ud;
    int len = (int)strlen(buf);
    if (!len) return;
    for (int i = 0; i < cmd_count; i++)
        if (_strnicmp(cmd_cache[i], buf, len) == 0)
            linenoiseAddCompletion(lc, cmd_cache[i]);
}

static const char *hints[][2] = {
    {"PING",        " [message]"},
    {"INFO",        " [section]"},
    {"DUMP",        ""},
    {"JOURNAL",     " [N | +offset [N] | ALL | CLEAR]"},
    {"KSET",        " key value [NX|XX]"},
    {"KGET",        " key"},
    {"KMGET",       " key [key ...]"},
    {"KMSET",       " key value [key value ...]"},
    {"KDEL",        " key [key ...]"},
    {"KEXISTS",     " key [key ...]"},
    {"KKEYS",       " pattern"},
    {"KGETALL",     " [prefix]"},
    {"IADD",        " name driver [address] [key=value ...]"},
    {"IREMOVE",     " name"},
    {"IINIT",       " name [config_file]"},
    {"IALIGN",      " name [name ...]"},
    {"IRESET",      " name"},
    {"IINFO",       " name"},
    {"IRESOURCES",  " name"},
    {"IREAD",       " instrument:resource"},
    {"IWRITE",      " instrument:resource value"},
    {"IRAW",        " instrument command"},
    {"IMREAD",      " resource [resource ...]"},
    {"ILOAD",       " instrument target file_path"},
    {"ISAVE",       " instrument target file_path"},
    {"ILOCK",       " instrument [instrument ...]"},
    {"IUNLOCK",     " instrument [...] | ALL"},
    {"SUBSCRIBE",   " channel [channel ...]"},
    {"ALIAS SET",   " name type target"},
    {"ALIAS GET",   " name"},
    {"ALIAS DEL",   " name"},
    {"AREAD",       " alias_name"},
    {"AWRITE",      " alias_name value"},
    {"CLIENT NAME", " [name]"},
    {"COMMAND LIST", " [pattern]"},
    {NULL, NULL}
};

static char *hints_cb(const char *buf, int *color, int *bold, void *ud) {
    (void)ud;
    *color = 90;
    *bold = 0;
    for (int i = 0; hints[i][0]; i++)
        if (_stricmp(buf, hints[i][0]) == 0)
            return _strdup(hints[i][1]);
    return NULL;
}

static void free_hints_cb(void *h, void *ud) { (void)ud; free(h); }

/* -- Network -- */

static int recv_full(SOCKET s, char *buf, int sz) {
    int total = 0, n;
    n = recv(s, buf, sz - 1, 0);
    if (n <= 0) return n;
    total = n;

    u_long nb = 1;
    ioctlsocket(s, FIONBIO, &nb);
    while (total < sz - 1) {
        n = recv(s, buf + total, sz - 1 - total, 0);
        if (n > 0) { total += n; continue; }
        fd_set fds;
        struct timeval tv = {0, 2000};
        FD_ZERO(&fds);
        FD_SET(s, &fds);
        if (select(0, &fds, NULL, NULL, &tv) <= 0) break;
    }
    nb = 0;
    ioctlsocket(s, FIONBIO, &nb);
    buf[total] = '\0';
    return total;
}

/* -- RESP display -- */

static int show_resp(const char *r, int len, int p, int idx) {
    if (p >= len) return p;
    char pfx[16] = "";
    if (idx > 0) sprintf(pfx, "%2d) ", idx);

    switch (r[p]) {
    case '+': {
        p++;
        int s = p;
        while (p < len && r[p] != '\r') p++;
        out("%s%.*s\n", pfx, p - s, r + s);
        if (p < len) p += 2;
        break;
    }
    case '-': {
        p++;
        int s = p;
        while (p < len && r[p] != '\r') p++;
        cset(C_RED);
        out("%s(error) %.*s\n", pfx, p - s, r + s);
        cset(C_RST);
        if (p < len) p += 2;
        break;
    }
    case ':': {
        p++;
        int s = p;
        while (p < len && r[p] != '\r') p++;
        out("%s(integer) %.*s\n", pfx, p - s, r + s);
        if (p < len) p += 2;
        break;
    }
    case '$': {
        p++;
        int s = p;
        while (p < len && r[p] != '\r') p++;
        int slen = atoi(r + s);
        if (p < len) p += 2;
        if (slen == -1) out("%s(nil)\n", pfx);
        else if (slen >= 0 && p + slen <= len) {
            out("%s\"%.*s\"\n", pfx, slen, r + p);
            p += slen;
            if (p < len) p += 2;
        }
        break;
    }
    case '*': {
        p++;
        int s = p;
        while (p < len && r[p] != '\r') p++;
        int cnt = atoi(r + s);
        if (p < len) p += 2;
        if (cnt <= 0) out("%s(empty)\n", pfx);
        else for (int i = 0; i < cnt && p < len; i++)
            p = show_resp(r, len, p, i + 1);
        break;
    }
    default:
        out("%s%s\n", pfx, r + p);
        p = len;
    }
    return p;
}

/* -- MONITOR mode -- */

static void monitor_loop(SOCKET s, char *buf, int sz) {
    cset(C_GRY);
    printf("monitor mode — press any key to stop\n");
    cset(C_RST);

    fd_set fds;
    struct timeval tv;
    while (1) {
        if (_kbhit()) { _getch(); break; }

        FD_ZERO(&fds);
        FD_SET(s, &fds);
        tv.tv_sec = 0;
        tv.tv_usec = 100000;

        int r = select(0, &fds, NULL, NULL, &tv);
        if (r == SOCKET_ERROR) break;

        if (r > 0 && FD_ISSET(s, &fds)) {
            int n = recv(s, buf, sz - 1, 0);
            if (n <= 0) { printf(C_RED "connection lost\n" C_RST); break; }
            buf[n] = '\0';

            char *line = buf;
            while (*line) {
                if (*line == '+') line++;
                char *eol = strstr(line, "\r\n");
                if (eol) {
                    *eol = '\0';
                    cset(C_YEL);
                    printf("%s\n", line);
                    cset(C_RST);
                    line = eol + 2;
                } else {
                    if (*line) { cset(C_YEL); printf("%s\n", line); cset(C_RST); }
                    break;
                }
            }
            fflush(stdout);
        }
    }

    /* Send PING to exit server-side monitor mode */
    const char *ping = "PING\r\n";
    send(s, ping, (int)strlen(ping), 0);
    recv_full(s, buf, sz);  /* consume +PONG */

    cset(C_GRY);
    printf("monitor stopped\n");
    cset(C_RST);
}

/* -- SUBSCRIBE mode -- */

static void subscribe_loop(SOCKET s, char *buf, int sz) {
    cset(C_GRY);
    printf("subscriber mode — type UNSUBSCRIBE to exit\n");
    cset(C_RST);

    fd_set fds;
    struct timeval tv;
    char input[BUF_SIZE] = {0};
    int ipos = 0;

    while (1) {
        if (_kbhit()) {
            int ch = _getch();
            if (ch == '\r' || ch == '\n') {
                input[ipos] = '\0';
                printf("\n");
                if (ipos == 0) continue;

                char sendbuf[BUF_SIZE + 3];
                snprintf(sendbuf, sizeof(sendbuf), "%.*s\r\n", ipos, input);
                if (send(s, sendbuf, (int)strlen(sendbuf), 0) == SOCKET_ERROR) break;

                /* Check if unsubscribe */
                char upper[BUF_SIZE];
                strncpy(upper, input, BUF_SIZE - 1);
                upper[BUF_SIZE - 1] = '\0';
                for (int i = 0; upper[i]; i++) upper[i] = toupper((unsigned char)upper[i]);

                if (strncmp(upper, "UNSUBSCRIBE", 11) == 0) {
                    int n = recv_full(s, buf, sz);
                    if (n > 0) show_resp(buf, n, 0, 0);
                    break;
                }
                ipos = 0;
            } else if (ch == '\b' || ch == 127) {
                if (ipos > 0) { ipos--; printf("\b \b"); }
            } else if (ipos < BUF_SIZE - 2) {
                input[ipos++] = (char)ch;
                printf("%c", ch);
            }
            continue;
        }

        FD_ZERO(&fds);
        FD_SET(s, &fds);
        tv.tv_sec = 0;
        tv.tv_usec = 100000;

        int r = select(0, &fds, NULL, NULL, &tv);
        if (r == SOCKET_ERROR) break;

        if (r > 0 && FD_ISSET(s, &fds)) {
            int n = recv(s, buf, sz - 1, 0);
            if (n <= 0) { printf(C_RED "connection lost\n" C_RST); break; }
            buf[n] = '\0';
            cset(C_CYN);
            int p = 0;
            while (p < n) p = show_resp(buf, n, p, 0);
            cset(C_RST);
            fflush(stdout);
        }
    }

    cset(C_GRY);
    printf("subscriber mode stopped\n");
    cset(C_RST);
}

/* -- Connection -- */

static SOCKET do_connect(const char *host, int port, struct sockaddr_in *a) {
    SOCKET s;
    int attempt = 0;
    while (1) {
        s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
        if (s == INVALID_SOCKET) return INVALID_SOCKET;
        if (connect(s, (struct sockaddr *)a, sizeof(*a)) == 0) {
            if (attempt > 0) printf("\n");
            return s;
        }
        closesocket(s);
        attempt++;
        if (attempt == 1) printf(C_YEL "waiting for server..." C_RST "\n");
        else { printf("\rretry #%d...", attempt); fflush(stdout); }
        Sleep(RECONNECT_MS);
    }
}

/* -- Query client ID from server -- */

static int get_client_id(SOCKET s, char *buf, int sz) {
    const char *q = "CLIENT ID\r\n";
    if (send(s, q, (int)strlen(q), 0) == SOCKET_ERROR) return 0;
    int n = recv_full(s, buf, sz);
    if (n > 0 && buf[0] == ':') return atoi(buf + 1);
    return 0;
}

/* -- Build prompt string -- */

static void make_prompt(char *prompt, int sz, int cid, const char *cname) {
    if (cname && cname[0])
        snprintf(prompt, sz, C_CYN "%s#%d" C_RST " > ", cname, cid);
    else
        snprintf(prompt, sz, C_CYN "testcore#%d" C_RST " > ", cid);
}

/* -- Main -- */

int main(int argc, char *argv[]) {
    const char *host = DEFAULT_HOST;
    int port = DEFAULT_PORT;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "-h") == 0 && i + 1 < argc) host = argv[++i];
        else if (strcmp(argv[i], "-p") == 0 && i + 1 < argc) port = atoi(argv[++i]);
        else if (strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [-h host] [-p port]\n", argv[0]);
            return 0;
        } else {
            printf("Usage: %s [-h host] [-p port]\n", argv[0]);
            return 1;
        }
    }

    char *resp = (char *)malloc(RECV_SIZE);
    if (!resp) { printf("memory error\n"); return 1; }

    SetConsoleCP(65001);
    SetConsoleOutputCP(65001);
    enable_vt();

    WSADATA wsa;
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        printf("winsock error\n");
        free(resp);
        return 1;
    }

    struct sockaddr_in addr;
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = inet_addr(host);
    addr.sin_port = htons((u_short)port);

    SOCKET sock = do_connect(host, port, &addr);
    if (sock == INVALID_SOCKET) {
        printf("connection failed\n");
        WSACleanup();
        free(resp);
        return 1;
    }

    printf(C_GRN "connected" C_RST " %s:%d\n", host, port);

    /* Setup linenoise */
    linenoiseSetCompletionCallback(completion_cb, NULL);
    linenoiseSetHintsCallback(hints_cb, NULL);
    linenoiseSetFreeHintsCallback(free_hints_cb);
    linenoiseHistorySetMaxLen(100);

    /* Fetch commands for TAB + get client ID */
    cache_fetch(sock, resp, RECV_SIZE);
    int client_id = get_client_id(sock, resp, RECV_SIZE);
    char client_name[128] = "";
    char prompt[256];
    make_prompt(prompt, sizeof(prompt), client_id, client_name);

    /* Main loop */
    char *line;
    while ((line = linenoise(prompt)) != NULL) {
        if (line[0] == '\0') { free(line); continue; }

        /* Uppercase copy for command matching (preserve original for send) */
        char upper[BUF_SIZE];
        strncpy(upper, line, BUF_SIZE - 1);
        upper[BUF_SIZE - 1] = '\0';
        for (int i = 0; upper[i]; i++)
            upper[i] = toupper((unsigned char)upper[i]);

        /* Local commands */
        if (strcmp(upper, "EXIT") == 0 || strcmp(upper, "QUIT") == 0) {
            free(line);
            break;
        }

        if (strcmp(upper, "CLEAR") == 0) {
            linenoiseClearScreen();
            free(line);
            continue;
        }

        /* Add to history */
        linenoiseHistoryAdd(line);

        /* Send to server (original case preserved) */
        {
            char sendbuf[BUF_SIZE + 3];
            snprintf(sendbuf, sizeof(sendbuf), "%s\r\n", line);
            if (send(sock, sendbuf, (int)strlen(sendbuf), 0) == SOCKET_ERROR) {
                closesocket(sock);
                printf(C_YEL "reconnecting..." C_RST "\n");
                sock = do_connect(host, port, &addr);
                if (sock == INVALID_SOCKET) { free(line); break; }
                printf(C_GRN "reconnected" C_RST "\n");
                cache_fetch(sock, resp, RECV_SIZE);
                client_id = get_client_id(sock, resp, RECV_SIZE);
                client_name[0] = '\0';
                make_prompt(prompt, sizeof(prompt), client_id, client_name);
                free(line);
                continue;
            }
        }

        /* Receive and display */
        int n = recv_full(sock, resp, RECV_SIZE);
        if (n > 0) {
            ob_start(RECV_SIZE);
            show_resp(resp, n, 0, 0);
            ob_end();

            /* Enter MONITOR mode */
            if (strcmp(upper, "MONITOR") == 0 && n >= 4 && strncmp(resp, "+OK", 3) == 0)
                monitor_loop(sock, resp, RECV_SIZE);

            /* Enter SUBSCRIBE mode */
            if (strncmp(upper, "SUBSCRIBE", 9) == 0 &&
                (upper[9] == ' ' || upper[9] == '\0') &&
                n > 0 && resp[0] == '*')
                subscribe_loop(sock, resp, RECV_SIZE);

            /* Track CLIENT NAME changes for prompt */
            if (strncmp(upper, "CLIENT NAME ", 12) == 0 && n >= 3 && strncmp(resp, "+OK", 3) == 0) {
                /* Extract name from original line (after "CLIENT NAME ") */
                const char *namearg = line + 12;
                while (*namearg == ' ') namearg++;
                strncpy(client_name, namearg, sizeof(client_name) - 1);
                client_name[sizeof(client_name) - 1] = '\0';
                /* Remove surrounding quotes if any */
                int nlen = (int)strlen(client_name);
                if (nlen >= 2 && client_name[0] == '"' && client_name[nlen - 1] == '"') {
                    memmove(client_name, client_name + 1, nlen - 2);
                    client_name[nlen - 2] = '\0';
                }
                make_prompt(prompt, sizeof(prompt), client_id, client_name);
            }
        } else {
            closesocket(sock);
            printf(C_YEL "reconnecting..." C_RST "\n");
            sock = do_connect(host, port, &addr);
            if (sock == INVALID_SOCKET) { free(line); break; }
            printf(C_GRN "reconnected" C_RST "\n");
            cache_fetch(sock, resp, RECV_SIZE);
            client_id = get_client_id(sock, resp, RECV_SIZE);
            client_name[0] = '\0';
            make_prompt(prompt, sizeof(prompt), client_id, client_name);
        }

        free(line);
    }

    closesocket(sock);
    WSACleanup();
    linenoiseHistoryFree();
    cache_clear();
    free(resp);
    return 0;
}
