/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
#include "protocol.h"
#undef NDEBUG
#include <assert.h>
#include <stdio.h>
#include <string.h>
typedef struct { uint8_t packet[RF_MAX], serial[4096]; size_t n, bytes; int sent,resets,led; } mock;
static int send_cb(void *v,uint16_t port,const uint8_t *p,size_t n) {
    mock *m=v; assert(port==1234 || port==4321); assert(n<=RF_MAX);
    memcpy(m->packet,p,n); m->n=n; m->sent++;
    assert(rf_crc(p,n-2)==((uint16_t)p[n-2]<<8|p[n-1])); return 0;
}
static int serial_cb(void *v,const uint8_t *p,size_t n) { mock *m=v; memcpy(m->serial,p,n); m->bytes+=n; return 0; }
static int reset_cb(void *v) { ((mock *)v)->resets++; return 0; }
static void led_cb(void *v,uint8_t x) { ((mock *)v)->led=x; }
static void command(rf_state *s,uint16_t port,uint8_t cmd,const uint8_t *p,size_t n,uint64_t time) {
    uint8_t b[RF_MAX]; size_t len=rf_pack(b,cmd,42,p,n); assert(len); assert(!rf_packet(s,port,b,len,time));
}
int main(void) {
    mock m={0}; rf_state s={.endpoint=1,.ctx=&m,.send=send_cb,.serial=serial_cb,.reset=reset_cb,.led=led_cb};
    uint8_t v2[]={2,0}, v1[]={1};
    command(&s,1234,0,v2,2,100);
    assert(s.peer==1234 && s.endpoint==3 && !s.started);
    assert(m.n==7 && m.packet[0]==0 && m.packet[2]==2 && m.packet[3]==42 && m.packet[4]==3);
    command(&s,4321,4,NULL,0,101); assert(!m.resets);
    command(&s,4321,0,v2,2,102); assert(s.peer==1234);
    command(&s,1234,4,NULL,0,103); assert(m.resets==1);
    uint8_t led=5; command(&s,1234,3,&led,1,104); assert(m.led==5);
    uint8_t raw[]={0xfd,0x00,0x03,0xfc,0x7d,0xfc,0x7c,0xff,0};
    command(&s,1234,7,raw,sizeof(raw),105); assert(m.bytes==sizeof(raw) && !memcmp(m.serial,raw,sizeof(raw)));
    int count=m.sent; assert(!rf_serial(&s,raw,sizeof(raw))); assert(m.sent==count);
    command(&s,1234,5,NULL,0,106); assert(s.started);
    assert(!rf_serial(&s,raw,sizeof(raw))); assert(m.packet[0]==7 && !memcmp(m.packet+2,raw,sizeof(raw)));
    assert(!rf_tick(&s,1100)); assert(m.packet[0]==2);
    assert(!rf_tick(&s,5107)); assert(!s.peer && s.started);
    uint8_t wrong[]={2,99}; command(&s,4321,0,wrong,2,5108); assert(!s.peer);
    uint8_t reconnect[]={2,3}; command(&s,4321,0,reconnect,2,5109); assert(s.peer==4321 && s.started);
    command(&s,4321,6,NULL,0,5110); assert(!s.started);
    command(&s,4321,1,NULL,0,5111); assert(!s.peer);
    command(&s,1234,0,v1,1,5112); assert(m.n==6 && m.packet[2]==1);
    uint8_t bad[RF_MAX+1]={0}; size_t n=rf_pack(bad,4,9,NULL,0); bad[3]^=1;
    count=m.resets; assert(!rf_packet(&s,1234,bad,n,5113)); assert(m.resets==count);
    for(size_t i=0;i<4;i++) assert(!rf_packet(&s,1234,bad,i,5114));
    assert(!rf_packet(&s,1234,bad,sizeof(bad),5114));
    command(&s,1234,4,raw,1,5115); assert(m.resets==count);
    command(&s,1234,5,NULL,0,5116);
    uint8_t large[3000]; memset(large,0xa5,sizeof(large)); count=m.sent;
    assert(!rf_serial(&s,large,sizeof(large))); assert(m.sent==count+3);
    assert(m.n==68); /* 3000 - 1468 - 1468 + framing */
    /* Fresh v2 connections reset START; resume preserves it. */
    command(&s,1234,0,v2,2,5117); assert(!s.started && s.endpoint==7);
    /* Deterministic malformed input exercise, never large allocations. */
    uint32_t seed=17;
    for(int i=0;i<20000;i++) {
        size_t len=(size_t)i%sizeof(bad);
        for(size_t j=0;j<len;j++) { seed=seed*1664525u+1013904223u; bad[j]=(uint8_t)(seed>>24); }
        assert(!rf_packet(&s,9999,bad,len,5118));
    }
    puts("PASS: v1/v2 handshake, echo/CRC, source isolation, reset/LED, byte fidelity, start/stop, timeout/reconnect, bounds, 20000 malformed packets");
    return 0;
}
