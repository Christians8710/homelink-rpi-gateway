/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
#ifndef RF_PROTOCOL_H
#define RF_PROTOCOL_H
#include <stddef.h>
#include <stdint.h>
#define RF_MAX 1472
typedef struct {
    uint16_t peer;
    uint8_t sequence, endpoint;
    int started;
    uint64_t last_rx, last_tx;
    void *ctx;
    int (*send)(void *, uint16_t, const uint8_t *, size_t);
    int (*serial)(void *, const uint8_t *, size_t);
    int (*reset)(void *);
    void (*led)(void *, uint8_t);
} rf_state;
uint16_t rf_crc(const uint8_t *, size_t);
size_t rf_pack(uint8_t *, uint8_t, uint8_t, const uint8_t *, size_t);
int rf_packet(rf_state *, uint16_t, const uint8_t *, size_t, uint64_t);
int rf_serial(rf_state *, const uint8_t *, size_t);
int rf_tick(rf_state *, uint64_t);
#endif
