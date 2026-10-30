/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
/* Raspberry Pi 4 RPI-RF-MOD userspace backend, 2026-10-30.
 * Uses Linux GPIO chardev v2 and the standard PL011 tty driver.
 * Pin assignments checked against Alexander Reinert's piVCCU overlay:
 * reset BCM19, RGB BCM16/20/21; HM-MOD-RPI-PCB BCM18 is NOT used here.
 * This adaptation is CC BY-NC-SA 4.0; see LICENSE.md and SOURCES.txt.
 */
#define _GNU_SOURCE
#include "pi_uart.h"
#include <errno.h>
#include <fcntl.h>
#include <linux/gpio.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <unistd.h>

static int reset_fd = -1, led_fd = -1;

#ifndef RFBRIDGE_TEST_BACKEND
static int request_lines(int chip, const unsigned *offsets, unsigned count,
                         unsigned long long flags) {
    struct gpio_v2_line_request request = {0};
    request.num_lines = count;
    request.config.flags = flags;
    snprintf(request.consumer, sizeof(request.consumer), "pi-rfbridge");
    for (unsigned i = 0; i < count; i++) request.offsets[i] = offsets[i];
    if (ioctl(chip, GPIO_V2_GET_LINE_IOCTL, &request)) return -1;
    return request.fd;
}

static int open_chip(void) {
    for (int i = 0; i < 16; i++) {
        char name[64]; snprintf(name, sizeof(name), "/dev/gpiochip%d", i);
        int fd = open(name, O_RDONLY | O_CLOEXEC);
        if (fd < 0) continue;
        struct gpiochip_info info = {0};
        if (!ioctl(fd, GPIO_GET_CHIPINFO_IOCTL, &info) &&
            !strcmp(info.label, "pinctrl-bcm2711") && info.lines >= 54) return fd;
        close(fd);
    }
    errno = ENODEV;
    return -1;
}

static int direction(int fd, unsigned long long flags) {
    struct gpio_v2_line_config config = {0};
    config.flags = flags;
    return ioctl(fd, GPIO_V2_LINE_SET_CONFIG_IOCTL, &config);
}
#endif

int pi_uart_init(int uart) {
    struct termios settings;
    if (tcgetattr(uart, &settings)) { perror("UART tcgetattr"); return -1; }
    cfmakeraw(&settings);
    if (cfsetispeed(&settings, B115200) || cfsetospeed(&settings, B115200)) return -1;
    settings.c_cflag &= ~(PARENB | CSTOPB | CSIZE | CRTSCTS);
    settings.c_cflag |= CS8 | CLOCAL | CREAD;
    settings.c_cc[VMIN] = 1; settings.c_cc[VTIME] = 0;
    if (tcsetattr(uart, TCSANOW, &settings) || tcflush(uart, TCIOFLUSH)) return -1;
    if (ioctl(uart, TIOCEXCL)) { perror("UART exclusive access"); return -1; }
#ifndef RFBRIDGE_TEST_BACKEND
    int chip = open_chip();
    if (chip < 0) { perror("Pi4 BCM2711 GPIO controller"); return -1; }
    const unsigned reset[] = {19}, leds[] = {16, 20, 21};
    reset_fd = request_lines(chip, reset, 1, GPIO_V2_LINE_FLAG_INPUT);
    if (reset_fd >= 0) led_fd = request_lines(chip, leds, 3, GPIO_V2_LINE_FLAG_OUTPUT);
    close(chip);
    if (reset_fd < 0 || led_fd < 0) {
        perror("RPI-RF-MOD GPIO lines busy/unavailable");
        pi_uart_close(); return -1;
    }
#endif
    return 0;
}

int pi_uart_reset(void) {
#ifndef RFBRIDGE_TEST_BACKEND
    if (reset_fd < 0 || direction(reset_fd, GPIO_V2_LINE_FLAG_OUTPUT)) return -1;
    /* Assert low 50 ms, release high 50 ms, then leave reset as input. */
    usleep(50000);
    struct gpio_v2_line_values values = {.bits = 1, .mask = 1};
    if (ioctl(reset_fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &values)) {
        (void)direction(reset_fd, GPIO_V2_LINE_FLAG_INPUT); return -1;
    }
    usleep(50000);
    if (direction(reset_fd, GPIO_V2_LINE_FLAG_INPUT)) return -1;
    usleep(50000);
#endif
    return 0;
}

void pi_uart_led(uint8_t bits) {
#ifndef RFBRIDGE_TEST_BACKEND
    if (led_fd >= 0) {
        struct gpio_v2_line_values values = {.bits = bits & 7, .mask = 7};
        if (ioctl(led_fd, GPIO_V2_LINE_SET_VALUES_IOCTL, &values)) perror("Module LED");
    }
#else
    (void)bits;
#endif
}

void pi_uart_close(void) {
    if (reset_fd >= 0) close(reset_fd);
    if (led_fd >= 0) close(led_fd);
    reset_fd = led_fd = -1;
}
