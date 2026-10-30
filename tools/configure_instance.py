#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Create per-device settings without changing existing accounts or secrets."""
import argparse
import grp
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import time


def atomic(path, text, mode=0o600):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.homelink-new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,mode)
    with os.fdopen(fd,'w') as stream:
        stream.write(text);stream.flush();os.fsync(stream.fileno())
    os.chmod(temp,mode);temp.replace(path)


def configure(root, country='DE', repository='Christians8710/homelink-rpi-gateway', gpio=True):
    root=Path(root)
    base=root/'etc/zigbee-gateway';base.mkdir(parents=True,exist_ok=True)
    created={}
    if not (base/'setup.json').exists():
        macfile=root/'sys/class/net/wlan0/address'
        suffix=re.sub('[^0-9a-f]','',macfile.read_text().lower())[-6:] if macfile.exists() else secrets.token_hex(3)
        setup={'ssid':'HomeLink-Setup-'+suffix,'password':secrets.token_urlsafe(12),'country':country}
        atomic(base/'setup.json',json.dumps(setup,indent=2)+'\n')
        created.update(ssid=setup['ssid'],wifi_password=setup['password'])
    if not (base/'admin.json').exists():
        password=secrets.token_urlsafe(18);salt=secrets.token_hex(16)
        digest=hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1)
        atomic(base/'admin.json',json.dumps({'username':'admin','salt':salt,'hash':digest.hex()})+'\n')
        created.update(admin='admin',admin_password=password)
    if not (base/'serial.json').exists():
        device='/dev/ttyUSB0'
        folder=root/'dev/serial/by-id'
        if folder.is_dir():
            device=next(('/dev/serial/by-id/'+p.name for p in sorted(folder.iterdir()) if 'sonoff' in p.name.lower()),device)
        atomic(base/'serial.json',json.dumps({'device':device,'port':8888,'baudrate':115200,'rtscts':False},indent=2)+'\n')
    if not (base/'ser2net.yaml').exists():
        serial=json.loads((base/'serial.json').read_text())
        connector='serialdev,'+serial['device']+','+str(serial['baudrate'])+'n81,local'+(',rtscts' if serial['rtscts'] else '')
        text='%YAML 1.1\n---\ndefault:\n  name: mdns\n  value: false\nconnection: &zigbee\n  accepter: tcp,'+str(serial['port'])+'\n  enable: on\n  options:\n    kickolduser: true\n  connector: '+json.dumps(connector)+'\n'
        atomic(base/'ser2net.yaml',text,0o640)
    link=root/'etc/ser2net.yaml'
    if not link.is_symlink():
        if link.exists():
            backup=root/'var/backups/homelink/original-ser2net.yaml'
            backup.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            if not backup.exists():shutil.copyfile(link,backup);os.chmod(backup,0o600)
        temp=link.with_name('ser2net.yaml.homelink-new');temp.unlink(missing_ok=True)
        temp.symlink_to('zigbee-gateway/ser2net.yaml');temp.replace(link)
    if not (base/'rfbridge.json').exists():atomic(base/'rfbridge.json',json.dumps({'enabled':False,'ccu_ip':''},indent=2)+'\n')
    if not (base/'updates.json').exists():atomic(base/'updates.json',json.dumps({'repository':repository,'prereleases':False},indent=2)+'\n')
    boot=root/'boot/firmware'
    if gpio:
        config=boot/'config.txt';cmdline=boot/'cmdline.txt'
        if not config.is_file() or not cmdline.is_file():raise RuntimeError('Pi boot files missing')
        text=config.read_text()
        if '# BEGIN ZIGBEE-HOMEMATIC GATEWAY' not in text and '# BEGIN HOMELINK GPIO' not in text:
            text=text.rstrip()+'\n\n# BEGIN HOMELINK GPIO\n[all]\nenable_uart=1\ndtoverlay=disable-bt\n# END HOMELINK GPIO\n'
            atomic(config,text,0o644)
        words=[word for word in cmdline.read_text().split() if not re.match(r'^console=(serial0|ttyAMA0)(,|$)',word)]
        if not any(word.startswith('root=') for word in words):raise RuntimeError('Root filesystem missing in Pi cmdline')
        atomic(cmdline,' '.join(words)+'\n',0o644)
    if created:
        info=['HomeLink – private Zugangsdaten','']
        if 'ssid' in created:info+=['Einrichtungs-WLAN: '+created['ssid'],'WLAN-Passwort: '+created['wifi_password'],'WLAN-Einrichtung: http://192.168.4.1','']
        if 'admin' in created:info+=['Webverwaltung: http://<Pi-IP>/admin','Benutzername: admin','Admin-Passwort: '+created['admin_password'],'']
        info+=['SSH verwendet dein bereits eingerichtetes Raspberry-Pi-OS-Konto.','Diese Datei privat aufbewahren.']
        atomic(boot/'HomeLink-Zugangsdaten.txt','\n'.join(info)+'\n',0o600)
    return created


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--root',default='/');parser.add_argument('--country',default='DE');parser.add_argument('--without-gpio',action='store_true')
    args=parser.parse_args()
    if not re.fullmatch('[A-Z]{2}',args.country):raise SystemExit('Country must be a two-letter ISO code')
    if os.geteuid()!=0:raise SystemExit('Run with sudo')
    fresh=configure(args.root,args.country,gpio=not args.without_gpio)
    base=Path(args.root)/'etc/zigbee-gateway'
    dialout=grp.getgrnam('dialout').gr_gid
    os.chown(base,0,dialout);os.chmod(base,0o750)
    os.chown(base/'ser2net.yaml',0,dialout);os.chmod(base/'ser2net.yaml',0o640)
    print('HomeLink settings ready; existing credentials preserved.')
    if fresh:print('New credentials: /boot/firmware/HomeLink-Zugangsdaten.txt')
