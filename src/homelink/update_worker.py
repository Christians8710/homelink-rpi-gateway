#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Runs in its own systemd service, independently of the web server."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.request

from update_bundle import PACKAGE, atomic_json, installed_version, verify

JOB = Path(sys.argv[1]).resolve()
ROOT = Path('/var/lib/homelink/updates').resolve()
if JOB.parent != ROOT or len(JOB.name) != 32 or any(c not in '0123456789abcdef' for c in JOB.name):
    raise SystemExit('Invalid update job directory')
STATE = JOB/'state.json'
LOG = JOB/'install.log'
CACHE = Path('/var/cache/homelink/packages')
BACKUPS = Path('/var/backups/homelink')
WEB_CHANGED = False


def state(phase, message, progress, **more):
    data = json.loads(STATE.read_text())
    data.update(phase=phase, message=message, progress=progress, updated=time.time(), **more)
    atomic_json(STATE, data)


def command(args, check=True):
    with LOG.open('a') as log:
        result = subprocess.run(args, stdout=log, stderr=subprocess.STDOUT, env=dict(os.environ, DEBIAN_FRONTEND='noninteractive'))
    if check and result.returncode:
        raise RuntimeError('Ein Installationsschritt ist fehlgeschlagen. Details stehen im Protokoll.')
    return result.returncode


def cached(version):
    return CACHE/(hashlib.sha256(version.encode()).hexdigest()[:24]+'.deb')


def health():
    for service in ('ser2net', 'zigbee-setup'):
        if subprocess.run(['systemctl', 'is-active', '--quiet', service]).returncode:
            raise RuntimeError('Ein HomeLink-Dienst ist nach dem Update nicht aktiv.')
    if rf_was_active and subprocess.run(['systemctl', 'is-active', '--quiet', 'rfbridge']).returncode:
        raise RuntimeError('Die Homematic-Funkbrücke ist nach dem Update nicht aktiv.')


lock = open('/run/lock/homelink-update.lock', 'w')
fcntl.flock(lock, fcntl.LOCK_EX)
old_version = installed_version()
old_package = cached(old_version)
backup = BACKUPS/datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f')
rf_was_active = subprocess.run(['systemctl', 'is-active', '--quiet', 'rfbridge']).returncode == 0
changed_package = False
try:
    state('verifying', 'Updatepaket wird geprüft.', 10)
    manifest, package = verify(JOB/'package.hlupdate')
    if not old_package.is_file():
        raise RuntimeError('Die Sicherung des installierten Softwarepakets fehlt. Update nicht gestartet.')
    if subprocess.check_output(['dpkg', '--audit'], text=True).strip():
        raise RuntimeError('Die Paketverwaltung meldet einen offenen Vorgang. Update nicht gestartet.')
    backup.mkdir(parents=True, mode=0o700)
    shutil.copyfile(old_package, backup/'previous.deb')
    state('backup', 'Einstellungen und vorherige Version werden gesichert.', 25)
    with tarfile.open(backup/'configuration.tar', 'w') as archive:
        for folder in ('etc/zigbee-gateway', 'etc/NetworkManager/system-connections'):
            archive.add('/'+folder, arcname=folder)
    os.chmod(backup/'configuration.tar', 0o600)
    (JOB/'package.deb').write_bytes(package)
    state('installing', 'HomeLink wird aktualisiert.', 50)
    changed_package = True
    command(['dpkg', '--force-confold', '-i', str(JOB/'package.deb')])
    command(['systemctl', 'daemon-reload'])
    for service in manifest['restart_services']:
        if service != 'zigbee-setup':
            command(['systemctl', 'restart', service])
    state('checking', 'Neue Version wird geprüft.', 80)
    # The updater is a separate service; restarting the portal cannot kill it.
    if 'zigbee-setup' in manifest['restart_services']:
        command(['systemctl', 'restart', 'zigbee-setup'])
    for attempt in range(25):
        try:
            with urllib.request.urlopen('http://127.0.0.1/admin', timeout=2) as response:
                assert b'HomeLink' in response.read()
            break
        except (OSError, AssertionError):
            time.sleep(1)
    else:
        raise RuntimeError('Die Webverwaltung ist nach dem Update nicht erreichbar.')
    health()
    if installed_version() != manifest['package_version']:
        raise RuntimeError('Die installierte Version entspricht nicht dem Updatepaket.')
    CACHE.mkdir(parents=True, exist_ok=True, mode=0o700)
    shutil.copyfile(JOB/'package.deb', cached(manifest['package_version']))
    os.chmod(cached(manifest['package_version']), 0o600)
    state('success', 'HomeLink '+manifest['version']+' wurde erfolgreich installiert.', 100,
          version=manifest['version'], backup=str(backup), finished=time.time(), rolled_back=False)
except BaseException as error:
    restored = False
    if changed_package and (backup/'previous.deb').is_file():
        state('rollback', 'Vorherige Version wird wiederhergestellt.', 85)
        try:
            command(['dpkg', '--force-confold', '--force-downgrade', '-i', str(backup/'previous.deb')])
            with tarfile.open(backup/'configuration.tar') as archive:
                archive.extractall('/', filter='fully_trusted')
            command(['systemctl', 'daemon-reload'])
            command(['systemctl', 'restart', 'zigbee-setup'])
            restored = installed_version() == old_version
            health()
        except BaseException:
            restored = False
    message = str(error) if isinstance(error, (ValueError, RuntimeError)) else 'Die Installation ist fehlgeschlagen.'
    if restored:
        message += ' Die vorherige Version wurde wiederhergestellt.'
    state('failed', message, 100, rolled_back=restored, finished=time.time())
    raise SystemExit(1)
