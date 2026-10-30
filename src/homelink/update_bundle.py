# SPDX-License-Identifier: MIT
"""Verification for signed HomeLink update containers. No package is executed here."""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import tempfile
import zipfile

MAX_UPLOAD = 32 * 1024 * 1024
MAX_PACKAGE = 28 * 1024 * 1024
PUBLIC_KEY = Path('/usr/share/homelink/update-public-key.pem')
PACKAGE = 'zigbee-gateway-setup'


class UpdateError(ValueError):
    pass


def installed_version():
    return subprocess.check_output(['dpkg-query', '-W', '-f=${Version}', PACKAGE], text=True).strip()


def compare(left, operator, right):
    return subprocess.run(['dpkg', '--compare-versions', left, operator, right], capture_output=True).returncode == 0


def atomic_json(path, data):
    temp = path.with_name(path.name + '.tmp')
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    temp.replace(path)


def verify(bundle, public_key=PUBLIC_KEY, check_host=True):
    bundle = Path(bundle)
    if not bundle.is_file() or not 0 < bundle.stat().st_size <= MAX_UPLOAD:
        raise UpdateError('Update-Datei fehlt oder ist zu groß.')
    try:
        with zipfile.ZipFile(bundle) as archive:
            members = archive.infolist()
            if len(members) != 3 or {x.filename for x in members} != {'manifest.json', 'manifest.sig', 'package.deb'}:
                raise UpdateError('Kein gültiges HomeLink-Updatepaket.')
            sizes = {'manifest.json': 16384, 'manifest.sig': 64, 'package.deb': MAX_PACKAGE}
            for member in members:
                mode = member.external_attr >> 16
                if member.flag_bits & 1 or stat.S_ISLNK(mode) or not 0 < member.file_size <= sizes[member.filename]:
                    raise UpdateError('Ungültiger Paketinhalt.')
            raw = archive.read('manifest.json')
            signature = archive.read('manifest.sig')
            if len(signature) != 64:
                raise UpdateError('Ungültige Updatesignatur.')
            with tempfile.TemporaryDirectory(prefix='homelink-verify-') as temp:
                temp = Path(temp)
                (temp/'manifest.json').write_bytes(raw)
                (temp/'manifest.sig').write_bytes(signature)
                result = subprocess.run(['openssl', 'pkeyutl', '-verify', '-pubin', '-inkey', str(public_key),
                                         '-rawin', '-in', str(temp/'manifest.json'), '-sigfile', str(temp/'manifest.sig')],
                                        capture_output=True, timeout=10)
                if result.returncode:
                    raise UpdateError('Die Signatur ist ungültig. Das Paket wird nicht installiert.')
            manifest = json.loads(raw)
            required = {'format', 'product', 'version', 'package_version', 'package', 'architecture',
                        'platform', 'sha256', 'minimum_version', 'restart_services', 'changes'}
            if not isinstance(manifest, dict) or set(manifest) != required:
                raise UpdateError('Unbekanntes Updateformat.')
            if manifest['format'] != 1 or manifest['product'] != 'HomeLink' or manifest['package'] != PACKAGE:
                raise UpdateError('Dieses Update gehört nicht zu HomeLink.')
            if manifest['architecture'] != 'armhf' or manifest['platform'] != 'raspberry-pi-4b':
                raise UpdateError('Dieses Update passt nicht zum Raspberry Pi 4 B.')
            if not isinstance(manifest['version'], str) or not re.fullmatch(r'[0-9][0-9A-Za-z.+~-]{0,39}', manifest['version']):
                raise UpdateError('Ungültige Versionsangabe.')
            for field in ('package_version', 'minimum_version'):
                if not isinstance(manifest[field], str) or subprocess.run(['dpkg', '--validate-version', manifest[field]], capture_output=True).returncode:
                    raise UpdateError('Ungültige Paketversion.')
            if not isinstance(manifest['changes'], str) or len(manifest['changes']) > 8000:
                raise UpdateError('Ungültige Änderungsbeschreibung.')
            services = manifest['restart_services']
            if not isinstance(services, list) or len(set(services)) != len(services) or any(s not in ('zigbee-setup', 'ser2net', 'rfbridge') for s in services):
                raise UpdateError('Ungültiger Neustartumfang.')
            if not isinstance(manifest['sha256'], str) or not re.fullmatch(r'[a-f0-9]{64}', manifest['sha256']):
                raise UpdateError('Ungültige Prüfsumme.')
            package = archive.read('package.deb')
    except (zipfile.BadZipFile, KeyError, json.JSONDecodeError, RuntimeError, NotImplementedError) as error:
        raise UpdateError('Das Updatepaket ist beschädigt oder hat ein unbekanntes Format.') from error
    if hashlib.sha256(package).hexdigest() != manifest['sha256']:
        raise UpdateError('Die Prüfsumme des Pakets stimmt nicht.')
    if check_host:
        model = Path('/proc/device-tree/model').read_bytes().rstrip(b'\0').decode()
        arch = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
        if not model.startswith('Raspberry Pi 4 Model B') or arch != 'armhf':
            raise UpdateError('Dieses Update benötigt die HomeLink-Basis für den Pi 4 B (32 Bit).')
        current = installed_version()
        if compare(current, 'lt', manifest['minimum_version']):
            raise UpdateError('Zuerst eine unterstützte HomeLink-Basis installieren.')
        if compare(manifest['package_version'], 'lt', current):
            raise UpdateError('Eine ältere Version wird nicht über die installierte Version geschrieben.')
    with tempfile.TemporaryDirectory(prefix='homelink-deb-check-') as temp:
        path = Path(temp)/'package.deb'; path.write_bytes(package)
        actual = subprocess.check_output(['dpkg-deb', '-f', str(path), 'Package', 'Version', 'Architecture'], text=True)
        fields = dict(line.split(': ', 1) for line in actual.splitlines())
        if fields != {'Package': PACKAGE, 'Version': manifest['package_version'], 'Architecture': manifest['architecture']}:
            raise UpdateError('Paketmetadaten und signierte Beschreibung stimmen nicht überein.')
    return manifest, package
