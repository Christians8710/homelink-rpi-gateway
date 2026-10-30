/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
/* Linux adaptation of HB-RF-ETH's wire protocol.
 * Based on Alexander Reinert's HB-RF-ETH, copyright 2022.
 * Adaptation 2026. CC BY-NC-SA 4.0; see LICENSE.md and SOURCES.txt.
 */
#include "protocol.h"
#include <string.h>
uint16_t rf_crc(const uint8_t *p, size_t n) {
    uint16_t c = 0xd77f;
    while (n--) {
        c ^= (uint16_t)*p++ << 8;
        for (int i = 0; i < 8; ++i)
            c = (uint16_t)((c << 1) ^ ((c & 0x8000) ? 0x8005 : 0));
    }
    return c;
}
size_t rf_pack(uint8_t *out, uint8_t cmd, uint8_t seq, const uint8_t *p, size_t n) {
    if (n > RF_MAX - 4) return 0;
    out[0] = cmd; out[1] = seq;
    if (n) memcpy(out + 2, p, n);
    uint16_t c = rf_crc(out, n + 2);
    out[n + 2] = (uint8_t)(c >> 8); out[n + 3] = (uint8_t)c;
    return n + 4;
}
static int emit(rf_state *s, uint8_t cmd, const uint8_t *p, size_t n) {
    uint8_t out[RF_MAX];
    if (!s->peer) return 0;
    size_t len = rf_pack(out, cmd, s->sequence++, p, n);
    return len ? s->send(s->ctx, s->peer, out, len) : -1;
}
int rf_packet(rf_state *s, uint16_t port, const uint8_t *p, size_t n, uint64_t now) {
    if (!port || n < 4 || n > RF_MAX) return 0;
    if (rf_crc(p, n-2) != ((uint16_t)p[n-2] << 8 | p[n-1])) return 0;
    uint8_t cmd = p[0];
    if (cmd == 0) {
        int v1 = n == 5 && p[2] == 1;
        int v2 = n == 6 && p[2] == 2;
        if (!v1 && !v2) return 0;
        /* No takeover of a live session by another source port. */
        if (s->peer && s->peer != port) return 0;
        if (v2 && p[3] && p[3] != s->endpoint) return 0;
        if (v1 || p[3] == 0) {
            s->endpoint = (uint8_t)(s->endpoint + 2);
            s->started = 0;
        }
        s->peer = port; s->last_rx = now; s->last_tx = now;
        uint8_t answer[3] = {p[2], p[1], s->endpoint};
        return emit(s, 0, answer, v1 ? 2 : 3);
    }
    if (port != s->peer || !s->peer) return 0;
    if ((cmd == 3 && n != 5) || (cmd == 7 && n < 5) ||
        ((cmd == 1 || cmd == 2 || cmd == 4 || cmd == 5 || cmd == 6) && n != 4) || cmd > 7)
        return 0;
    s->last_rx = now;
    switch (cmd) {
    case 1: s->peer = 0; s->started = 0; break;
    case 2: break;
    case 3: if (s->led) s->led(s->ctx, p[2] & 7); break;
    case 4: return s->reset(s->ctx);
    case 5: s->started = 1; break;
    case 6: s->started = 0; break;
    /* TX must also work before START (matching original firmware). */
    case 7: return s->serial(s->ctx, p+2, n-4);
    }
    return 0;
}
int rf_serial(rf_state *s, const uint8_t *p, size_t n) {
    if (!s->peer || !s->started) return 0;
    /* The host driver consumes a byte stream; UART reads need not align
       with Homematic frames. Escaping and inner CRC are left untouched. */
    while (n) {
        size_t chunk = n > RF_MAX-4 ? RF_MAX-4 : n;
        if (emit(s, 7, p, chunk)) return -1;
        p += chunk; n -= chunk;
    }
    return 0;
}
int rf_tick(rf_state *s, uint64_t now) {
    if (!s->peer) return 0;
    if (now - s->last_rx > 5000) {
        s->peer = 0; /* Preserve START state for v2 reconnect. */
        return 0;
    }
    if (now - s->last_tx >= 1000) {
        s->last_tx = now;
        return emit(s, 2, NULL, 0);
    }
    return 0;
}
