#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build a Debian package and optionally sign an update; standard library only."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT=Path(__file__).resolve().parent.parent


def make_tar(entries):
    buffer=io.BytesIO()
    with tarfile.open(fileobj=buffer,mode='w:gz') as archive:
        dirs=set()
        for name in entries:dirs.update(x.as_posix() for x in Path(name).parents if str(x)!='.')
        for name in sorted(dirs,key=lambda x:(x.count('/'),x)):
            item=tarfile.TarInfo(name+'/');item.type=tarfile.DIRTYPE;item.mode=0o755;archive.addfile(item)
        for name,(data,mode) in sorted(entries.items()):
            item=tarfile.TarInfo(name);item.size=len(data);item.mode=mode;archive.addfile(item,io.BytesIO(data))
    return buffer.getvalue()


def ar(name,data):
    return f'{name+"/":<16}{0:<12}{0:<6}{0:<6}{"100644":<8}{len(data):<10}`\n'.encode()+data+(b'\n' if len(data)%2 else b'')


def build(output,binary=None,signing_key=None,openssl='openssl'):
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    version=(ROOT/'VERSION').read_text().strip();package_version=(ROOT/'PACKAGE_VERSION').read_text().strip()
    if binary is None:
        compiler=shutil.which('cc')
        if not compiler:raise RuntimeError('A native ARM compiler is required, or use --rfbridge-binary.')
        binary=output/'rfbridge-pi'
        subprocess.run([compiler,'-O2','-Wall','-Wextra','-Werror',str(ROOT/'rfbridge/bridge.c'),str(ROOT/'rfbridge/protocol.c'),str(ROOT/'rfbridge/pi_uart.c'),'-o',str(binary)],check=True)
    elf=Path(binary).read_bytes()
    if len(elf)<64 or elf[:5]!=b'\x7fELF\x01' or int.from_bytes(elf[18:20],'little')!=40 or b'/lib/ld-linux-armhf.so.3' not in elf:
        raise RuntimeError('RFBridge must be an ARM hard-float 32-bit executable.')
    files={}
    for source in (ROOT/'src/homelink').iterdir():
        if source.is_file() and source.suffix in ('.py','.html'):
            data=source.read_bytes()
            if source.suffix=='.py':compile(data,source.name,'exec')
            if source.suffix=='.html':data=data.replace(b'1.0-r1',version.encode()).replace(b'1.0.0',version.encode())
            files['opt/zigbee-gateway/'+source.name]=[data,0o644]
    files['usr/local/sbin/rfbridge-pi']=[elf,0o755]
    for source in (ROOT/'systemd').glob('*.service'):files['etc/systemd/system/'+source.name]=[source.read_bytes(),0o644]
    for name in ('version','admin-version'):files['usr/share/zigbee-gateway/'+name]=[(version+'\n').encode(),0o644]
    public=(ROOT/'assets/update-public-key.pem').read_bytes()
    files['usr/share/homelink/update-public-key.pem']=[public,0o644]
    files['etc/udev/rules.d/99-homelink-dongle.rules']=[b'SUBSYSTEM=="tty", ATTRS{idVendor}=="10c4", ATTRS{idProduct}=="ea60", ENV{ID_MM_DEVICE_IGNORE}="1"\n',0o644]
    for source in (ROOT/'LICENSES').iterdir():
        if source.is_file():files['usr/share/doc/zigbee-gateway-setup/licenses/'+source.name]=[source.read_bytes(),0o644]
    files['usr/share/doc/zigbee-gateway-setup/NOTICE']=[(ROOT/'NOTICE').read_bytes(),0o644]
    control=f'''Package: zigbee-gateway-setup
Version: {package_version}
Architecture: armhf
Maintainer: HomeLink Project <118019272+Christians8710@users.noreply.github.com>
Depends: python3, openssl, libc6 (>= 2.41), network-manager, ser2net (>= 4.6.8), dnsmasq-base, nftables, iw, rfkill, usbutils
Description: HomeLink Zigbee and Homematic network gateway
 Local web administration, Wi-Fi setup and signed software updates.
'''
    files['DEBIAN/control']=[control.encode(),0o644]
    files['DEBIAN/conffiles']=[(''.join('/'+name+'\n' for name in sorted(files) if name.startswith('etc/'))).encode(),0o644]
    files['DEBIAN/postinst']=[b'#!/bin/sh\nset -e\nsystemctl daemon-reload 2>/dev/null || true\nexit 0\n',0o755]
    files['DEBIAN/md5sums']=[(''.join(hashlib.md5(data).hexdigest()+'  '+name+'\n' for name,(data,mode) in sorted(files.items()) if name.startswith(('opt/','usr/')))).encode(),0o644]
    deb=b'!<arch>\n'+ar('debian-binary',b'2.0\n')+ar('control.tar.gz',make_tar({n[7:]:v for n,v in files.items() if n.startswith('DEBIAN/')}))+ar('data.tar.gz',make_tar({n:v for n,v in files.items() if not n.startswith('DEBIAN/')}))
    package=output/f'HomeLink-{version}_armhf.deb';package.write_bytes(deb)
    artifacts=[package]
    if signing_key:
        actual_public=subprocess.check_output([openssl,'pkey','-in',str(signing_key),'-pubout'])
        if actual_public.strip()!=public.strip():raise RuntimeError('Signing key does not match the published public key.')
        manifest={'format':1,'product':'HomeLink','version':version,'package_version':package_version,'package':'zigbee-gateway-setup','architecture':'armhf','platform':'raspberry-pi-4b','sha256':hashlib.sha256(deb).hexdigest(),'minimum_version':'1.2.1','restart_services':['zigbee-setup'],'changes':(ROOT/'RELEASE_NOTES.txt').read_text()}
        raw=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        with tempfile.TemporaryDirectory() as temp:
            temp=Path(temp);(temp/'manifest.json').write_bytes(raw)
            subprocess.run([openssl,'pkeyutl','-sign','-rawin','-inkey',str(signing_key),'-in',str(temp/'manifest.json'),'-out',str(temp/'manifest.sig')],check=True)
            signature=(temp/'manifest.sig').read_bytes()
        bundle=output/f'HomeLink-{version}.hlupdate'
        with zipfile.ZipFile(bundle,'w',zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('manifest.json',raw);archive.writestr('manifest.sig',signature);archive.writestr('package.deb',deb)
        artifacts.append(bundle)
    for artifact in artifacts:(output/(artifact.name+'.sha256')).write_text(hashlib.sha256(artifact.read_bytes()).hexdigest()+'  '+artifact.name+'\n',encoding='ascii')
    return artifacts


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='dist');parser.add_argument('--rfbridge-binary',type=Path);parser.add_argument('--signing-key',type=Path);parser.add_argument('--openssl',default='openssl')
    args=parser.parse_args()
    for artifact in build(args.output,args.rfbridge_binary,args.signing_key,args.openssl):print(artifact)
