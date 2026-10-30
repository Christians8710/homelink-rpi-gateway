import os, pty, tty, socket, subprocess, time, select, pathlib
def crc(b):
    # Independent polynomial long division, compared with C implementation.
    x=0xd77f
    for byte in b:
        x ^= byte << 8
        for _ in range(8):
            x <<= 1
            if x & 0x10000: x ^= 0x18005
    return x
counter=0
def packet(cmd,p=b''):
    global counter
    b=bytes((cmd,counter%256))+p; counter+=1
    return b+crc(b).to_bytes(2,'big')
def recv_type(s,cmd,timeout=2):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        s.settimeout(max(.01,deadline-time.monotonic()))
        b=s.recv(1600)
        assert crc(b[:-2])==int.from_bytes(b[-2:],'big')
        if b[0]==cmd: return b
    raise AssertionError('missing response '+str(cmd))
master,slave=pty.openpty(); tty.setraw(slave); device=os.ttyname(slave); os.close(slave)
log=open('/tmp/integration.log','wb')
p=subprocess.Popen(['/media/share/test-bridge-linux','--device',device,'--allow','127.0.0.1'],stderr=log)
s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('127.0.0.1',3008))
try:
    deadline=time.monotonic()+15
    while 'Listening UDP 3008' not in pathlib.Path('/tmp/integration.log').read_text():
        assert p.poll() is None, 'bridge exited before listen'
        assert time.monotonic()<deadline, 'bridge startup timeout'
        time.sleep(.1)
    connect=packet(0,b'\x02\x00'); s.send(connect)
    answer=recv_type(s,0); assert answer[2]==2 and answer[3]==connect[1]
    endpoint=answer[4]
    s.send(packet(4)); s.send(packet(5)); time.sleep(.1)
    raw=bytes(range(256))*3
    s.send(packet(7,raw)); got=bytearray(); deadline=time.monotonic()+3
    while len(got)<len(raw) and time.monotonic()<deadline:
        if select.select([master],[],[],.2)[0]: got.extend(os.read(master,4096))
    assert bytes(got)==raw
    os.write(master,raw)
    got=bytearray()
    while len(got)<len(raw): got.extend(recv_type(s,7)[2:-2])
    assert bytes(got)==raw
    bad=bytearray(packet(7,b'BAD')); bad[-1]^=1; s.send(bad); time.sleep(.2)
    assert not select.select([master],[],[],.1)[0]
    s.send(packet(6)); time.sleep(.1); os.write(master,b'DROP'); time.sleep(.2)
    s.send(packet(5)); time.sleep(.1); os.write(master,b'AFTER'); assert recv_type(s,7)[2:-2]==b'AFTER'
    # Let the endpoint expire while it keeps sending heartbeats.
    time.sleep(5.3)
    s.close(); s=socket.socket(socket.AF_INET,socket.SOCK_DGRAM); s.connect(('127.0.0.1',3008))
    s.send(packet(0,bytes((2,endpoint)))); recv_type(s,0)
    os.write(master,b'RESUME'); assert recv_type(s,7)[2:-2]==b'RESUME'
    s.send(packet(1)); time.sleep(.1)
    print('PASS: actual Linux UDP + PTY I/O; bidirectional 768-byte stream; reset callback; bad CRC; START/STOP; timeout and v2 reconnect')
finally:
    p.terminate(); p.wait(timeout=5); s.close(); os.close(master); log.close()
    print(pathlib.Path('/tmp/integration.log').read_text())
