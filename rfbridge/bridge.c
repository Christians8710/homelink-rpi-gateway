/* SPDX-License-Identifier: CC-BY-NC-SA-4.0 */
#define _GNU_SOURCE
#include "protocol.h"
#include "pi_uart.h"
#include <arpa/inet.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>
#define RAW_INFO _IOW('u', 0x82, char[64])
#define RAW_RESET _IO('u', 0x81)
static volatile sig_atomic_t stopping;
typedef struct {
    int udp, uart;
    struct in_addr owner;
    unsigned long long rx, tx, resets;
} context;
static uint64_t now_ms(void) {
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (uint64_t)t.tv_sec*1000 + (uint64_t)t.tv_nsec/1000000;
}
static void stop_signal(int sig) { (void)sig; stopping = 1; }
static int send_udp(void *v, uint16_t port, const uint8_t *p, size_t n) {
    context *c=v;
    struct sockaddr_in a={.sin_family=AF_INET,.sin_port=htons(port),.sin_addr=c->owner};
    ssize_t r=sendto(c->udp,p,n,0,(struct sockaddr *)&a,sizeof(a));
    if (r != (ssize_t)n) { perror("UDP send"); return -1; }
    return 0;
}
static int write_uart(void *v, const uint8_t *p, size_t n) {
    context *c=v;
    while (n && !stopping) {
        ssize_t r=write(c->uart,p,n);
        if (r<0 && errno==EINTR && !stopping) continue;
        if (r<=0) { perror("UART write"); return -1; }
        p+=r; n-=(size_t)r; c->tx+=(size_t)r;
    }
    return n ? -1 : 0;
}
static int reset_uart(void *v) {
    context *c=v;
    if (pi_uart_reset()<0) { perror("radio reset (another process using module?)"); return -1; }
    c->resets++; fprintf(stderr,"RPI-RF-MOD reset via BCM19\n");
    return 0;
}
static void led_uart(void *v, uint8_t bits) {
    (void)v; pi_uart_led(bits);
}
static int busy_device(dev_t device) {
    DIR *d=opendir("/proc"); if(!d) return -1;
    struct dirent *e; int busy=0;
    while((e=readdir(d))) {
        char *end; long pid=strtol(e->d_name,&end,10);
        if(*end || pid<=0 || pid==getpid()) continue;
        char dir[96]; snprintf(dir,sizeof(dir),"/proc/%ld/fd",pid);
        DIR *fds=opendir(dir); if(!fds) continue;
        struct dirent *f;
        while((f=readdir(fds))) {
            char p[384]; struct stat st;
            snprintf(p,sizeof(p),"%s/%s",dir,f->d_name);
            if(!stat(p,&st) && S_ISCHR(st.st_mode) && st.st_rdev==device) {
                fprintf(stderr,"Device already open by PID %ld\n",pid); busy=1; break;
            }
        }
        closedir(fds);
    }
    closedir(d); return busy;
}
static int local_peer(struct in_addr a) {
    uint32_t h=ntohl(a.s_addr);
    int private_ip=(h>>24)==10 || (h>>20)==0xac1 || (h>>16)==0xc0a8;
    if(!private_ip) return 0;
    struct ifaddrs *all=NULL;
    if(getifaddrs(&all)) return 0;
    int ok=0;
    for(struct ifaddrs *i=all;i;i=i->ifa_next) {
        if(!i->ifa_addr || !i->ifa_netmask || i->ifa_addr->sa_family!=AF_INET ||
           !(i->ifa_flags&IFF_UP) || (i->ifa_flags&IFF_LOOPBACK)) continue;
        uint32_t own=((struct sockaddr_in *)i->ifa_addr)->sin_addr.s_addr;
        uint32_t mask=((struct sockaddr_in *)i->ifa_netmask)->sin_addr.s_addr;
        if(a.s_addr!=own && (a.s_addr&mask)==(own&mask)) ok=1;
    }
    freeifaddrs(all); return ok;
}
int main(int argc,char **argv) {
    const char *dev="/dev/serial0"; const char *allow=NULL;
    int check=0;
    for(int i=1;i<argc;i++) {
        if(!strcmp(argv[i],"--device") && i+1<argc) dev=argv[++i];
        else if(!strcmp(argv[i],"--allow") && i+1<argc) allow=argv[++i];
        else if(!strcmp(argv[i],"--check")) check=1;
        else { fprintf(stderr,"Usage: %s [--device /dev/raw-uart] [--allow CCU_IPV4] [--check]\n",argv[0]); return 2; }
    }
    struct in_addr permitted={0};
    if(allow && inet_pton(AF_INET,allow,&permitted)!=1) { fprintf(stderr,"Invalid --allow IPv4\n"); return 2; }
    if(geteuid()!=0) { fprintf(stderr,"Run as root to inspect device ownership and use raw-uart\n"); return 2; }
    struct stat st;
    if(stat(dev,&st) || !S_ISCHR(st.st_mode)) { fprintf(stderr,"Missing Pi UART character device: %s\n",dev); return 2; }
    int lock=open("/run/rfbridge/rfbridge.lock",O_CREAT|O_RDWR|O_CLOEXEC,0600);
    if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)) { fprintf(stderr,"Another bridge is running\n"); return 2; }
    if(busy_device(st.st_rdev)!=0) return 2;
    context c={.udp=-1,.uart=-1};
    c.uart=open(dev,O_RDWR|O_NONBLOCK|O_CLOEXEC);
    if(c.uart<0) { perror(dev); return 2; }
    if(pi_uart_init(c.uart)<0) { close(c.uart); return 2; }
    fprintf(stderr,"Pi RFBridge 0.3 experimental; UART %s; RPI-RF-MOD BCM19 reset\n",dev);
    if(check) { pi_uart_close(); close(c.uart); return 0; }
    struct sigaction sa={0}; sa.sa_handler=stop_signal; sigemptyset(&sa.sa_mask);
    sigaction(SIGINT,&sa,NULL); sigaction(SIGTERM,&sa,NULL); sigaction(SIGALRM,&sa,NULL);
    c.udp=socket(AF_INET,SOCK_DGRAM|SOCK_NONBLOCK|SOCK_CLOEXEC,0);
    struct sockaddr_in bindaddr={.sin_family=AF_INET,.sin_port=htons(3008),.sin_addr={.s_addr=INADDR_ANY}};
    if(c.udp<0 || bind(c.udp,(struct sockaddr *)&bindaddr,sizeof(bindaddr))) { perror("UDP bind 3008"); close(c.uart); return 2; }
    rf_state s={.endpoint=1,.ctx=&c,.send=send_udp,.serial=write_uart,.reset=reset_uart,.led=led_uart};
    int random=open("/dev/urandom",O_RDONLY|O_CLOEXEC);
    if(random>=0) { (void)read(random,&s.endpoint,1); close(random); }
    s.endpoint|=1;
    fprintf(stderr,"Listening UDP 3008; allowed: %s; no authentication in HB-RF-ETH protocol\n",allow?allow:"private IPv4 on directly connected LAN");
    uint64_t stats=now_ms(), ownership=stats, report=0; int result=0;
    while(!stopping) {
        alarm(4); /* Interrupt a stuck kernel operation, then exit. */
        uint64_t now=now_ms();
        uint16_t oldpeer=s.peer;
        if(rf_tick(&s,now)) { result=1; break; }
        if(oldpeer && !s.peer) fprintf(stderr,"Peer timed out; waiting for reconnect\n");
        if(now-ownership>2000) {
            ownership=now;
            if(busy_device(st.st_rdev)!=0) { result=1; break; }
        }
        struct pollfd fds[2]={{.fd=c.udp,.events=POLLIN},{.fd=c.uart,.events=POLLIN}};
        int n=poll(fds,2,100);
        if(n<0) { if(errno==EINTR) continue; perror("poll"); result=1; break; }
        if((fds[0].revents|fds[1].revents)&(POLLERR|POLLHUP|POLLNVAL)) { fprintf(stderr,"Device/socket error\n"); result=1; break; }
        /* Drain local input before processing START, discarding stale data. */
        if(fds[1].revents&POLLIN) {
            uint8_t b[1468]; ssize_t r=read(c.uart,b,sizeof(b));
            if(r>0) { c.rx+=(size_t)r; if(rf_serial(&s,b,(size_t)r)) { result=1; break; } }
            else if(r==0 || (errno!=EAGAIN && errno!=EINTR)) { perror("UART read"); result=1; break; }
        }
        if(fds[0].revents&POLLIN) {
            uint8_t b[RF_MAX+1]; struct sockaddr_in a={0}; socklen_t alen=sizeof(a);
            ssize_t r=recvfrom(c.udp,b,sizeof(b),0,(struct sockaddr *)&a,&alen);
            if(r<0) { if(errno==EAGAIN || errno==EINTR) continue; perror("UDP receive"); result=1; break; }
            if((ntohl(a.sin_addr.s_addr)>>8)==0xc0a804) continue;
            if(allow ? a.sin_addr.s_addr!=permitted.s_addr : !local_peer(a.sin_addr)) continue;
            if(s.peer && a.sin_addr.s_addr!=c.owner.s_addr) continue;
            if(!s.peer) c.owner=a.sin_addr;
            oldpeer=s.peer;
            if(rf_packet(&s,ntohs(a.sin_port),b,(size_t)r,now_ms())) { result=1; break; }
            if(!oldpeer && s.peer) fprintf(stderr,"Connected %s:%u\n",inet_ntoa(c.owner),s.peer);
        }
        if(now-report>=1000) {
            report=now;
            FILE *state=fopen("/run/rfbridge/status.json.new","w");
            if(state) {
                fprintf(state,"{\"peer\":\"%s\",\"active\":%s,\"rx\":%llu,\"tx\":%llu,\"resets\":%llu,\"monotonic_ms\":%llu}\n",
                        s.peer?inet_ntoa(c.owner):"",s.started?"true":"false",c.rx,c.tx,c.resets,(unsigned long long)now);
                if(!fclose(state)) (void)rename("/run/rfbridge/status.json.new","/run/rfbridge/status.json");
            }
        }
        if(now-stats>=30000) {
            stats=now;
            fprintf(stderr,"Status peer=%s active=%d UART rx=%llu tx=%llu resets=%llu\n",s.peer?inet_ntoa(c.owner):"none",s.started,c.rx,c.tx,c.resets);
        }
    }
    alarm(0); pi_uart_close(); close(c.udp); close(c.uart); close(lock);
    fprintf(stderr,"Bridge stopped\n"); return result;
}
