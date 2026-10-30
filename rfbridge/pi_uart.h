/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
#ifndef PI_UART_H
#define PI_UART_H
#include <stdint.h>
int pi_uart_init(int uart);
int pi_uart_reset(void);
void pi_uart_led(uint8_t bits);
void pi_uart_close(void);
#endif
