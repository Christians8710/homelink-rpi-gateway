# SPDX-License-Identifier: MIT
"""Authenticated upload, release discovery and independent update jobs."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from update_bundle import MAX_UPLOAD, UpdateError, atomic_json, verify

ROOT = Path('/var/lib/homelink/updates')
CONFIG = Path('/etc/zigbee-gateway/updates.json')
LOCK = threading.RLock()
BUSY = {'queued', 'verifying', 'backup', 'installing', 'checking', 'rollback'}
SLUG = re.compile(r'[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.-]{1,100}')


def config():
    return json.loads(CONFIG.read_text()) if CONFIG.exists() else {'repository': '', 'prereleases': True}


def job_path(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise UpdateError('Ungültiger Updatevorgang.')
    return ROOT/identifier


def public_state(folder):
    data = json.loads((folder/'state.json').read_text())
    result = {key: data[key] for key in ('id', 'phase', 'message', 'progress', 'version', 'created',
                                       'updated', 'finished', 'rolled_back', 'changes') if key in data}
    if (folder/'install.log').exists():
        with (folder/'install.log').open('rb') as stream:
            stream.seek(max(0, (folder/'install.log').stat().st_size - 12000))
            result['log'] = stream.read(12000).decode(errors='replace')
    return result


def latest():
    path = ROOT/'latest.json'
    if not path.exists():
        return None
    try:
        folder = job_path(json.loads(path.read_text())['id'])
        result = public_state(folder)
        result['token'] = json.loads((folder/'state.json').read_text())['token']
        return result
    except (OSError, ValueError, KeyError):
        return None


def ensure_idle():
    current = latest()
    if current and current['phase'] in BUSY:
        raise UpdateError('Ein Update läuft bereits. Bitte warten.')


def clean_old():
    folders = sorted((p for p in ROOT.iterdir() if p.is_dir() and re.fullmatch(r'[a-f0-9]{32}', p.name)),
                     key=lambda p: p.stat().st_mtime, reverse=True)
    for folder in folders[3:]:
        try:
            state = json.loads((folder/'state.json').read_text())
            if state['phase'] not in BUSY:
                shutil.rmtree(folder)
        except (OSError, ValueError, KeyError):
            continue


def staged_bundle(path, peer):
    manifest, package = verify(path)
    folder = path.parent
    for name in ('update_worker.py', 'update_bundle.py'):
        shutil.copyfile(Path('/opt/zigbee-gateway')/name, folder/name)
    data = {'id': folder.name, 'phase': 'ready', 'message': 'Signatur und Kompatibilität geprüft.',
            'progress': 0, 'version': manifest['version'], 'changes': manifest['changes'],
            'created': time.time(), 'token': secrets.token_urlsafe(32), 'peer': peer}
    atomic_json(folder/'state.json', data)
    atomic_json(ROOT/'latest.json', {'id': folder.name})
    result = public_state(folder); result['token'] = data['token']
    return result


def upload(handler):
    if handler.headers.get('Transfer-Encoding') or handler.headers.get('Content-Type', '').split(';')[0] != 'application/octet-stream':
        raise UpdateError('Eine HomeLink-Updatedatei auswählen.')
    size = int(handler.headers.get('Content-Length', '0'))
    if not 0 < size <= MAX_UPLOAD:
        raise UpdateError('Die Updatedatei darf höchstens 32 MiB groß sein.')
    with LOCK:
        ensure_idle()
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        clean_old()
        folder = ROOT/secrets.token_hex(16)
        folder.mkdir(mode=0o700)
        handler.connection.settimeout(25)
        try:
            fd = os.open(folder/'package.hlupdate', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                remaining = size
                while remaining:
                    chunk = handler.rfile.read(min(65536, remaining))
                    if not chunk:
                        raise UpdateError('Die Übertragung wurde unterbrochen.')
                    stream.write(chunk); remaining -= len(chunk)
            return staged_bundle(folder/'package.hlupdate', handler.client_address[0])
        except BaseException:
            shutil.rmtree(folder)
            raise


def start(identifier):
    with LOCK:
        ensure_idle()
        folder = job_path(identifier)
        data = json.loads((folder/'state.json').read_text())
        if data['phase'] != 'ready':
            raise UpdateError('Dieses Update ist nicht bereit zur Installation.')
        verify(folder/'package.hlupdate')
        data.update(phase='queued', message='Installation wird gestartet.', progress=5)
        atomic_json(folder/'state.json', data)
        atomic_json(ROOT/'latest.json', {'id': folder.name})
        result = subprocess.run(['systemd-run', '--collect', '--quiet', '--unit=homelink-update-'+folder.name,
                                 '--property=Type=exec', '--property=UMask=0077', '--property=NoNewPrivileges=yes',
                                 '/usr/bin/python3', str(folder/'update_worker.py'), str(folder)],
                                capture_output=True, text=True, timeout=15)
        if result.returncode:
            data.update(phase='ready', message='Der Updateprozess konnte nicht gestartet werden.')
            atomic_json(folder/'state.json', data)
            raise UpdateError(data['message'])
        return {'id': folder.name, 'token': data['token'], 'message': 'Update gestartet.'}


def progress(identifier, token, peer):
    folder = job_path(identifier)
    data = json.loads((folder/'state.json').read_text())
    if not hmac.compare_digest(data.get('token', ''), token) or not token or data['peer'] != peer:
        raise UpdateError('Ungültiger Zugriff auf den Updatevorgang.')
    return public_state(folder)


def state():
    return {'version': Path('/usr/share/zigbee-gateway/admin-version').read_text().strip(),
            'config': config(), 'job': latest(), 'max_upload': MAX_UPLOAD}


def save_config(data):
    repository, prereleases = data.get('repository', ''), data.get('prereleases', True)
    if not isinstance(repository, str) or type(prereleases) is not bool:
        raise UpdateError('Ungültige GitHub-Einstellung.')
    repository = repository.strip()
    if repository and not SLUG.fullmatch(repository):
        raise UpdateError('GitHub-Repository als Benutzername/Repository angeben.')
    if repository.endswith(('/.', '/..')):
        raise UpdateError('Ungültiges GitHub-Repository.')
    atomic_json(CONFIG, {'repository': repository, 'prereleases': prereleases})
    return {'message': 'Updatequelle gespeichert.', 'config': config()}


def allowed_url(url):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and parsed.hostname in ('api.github.com', 'github.com', 'objects.githubusercontent.com',
                                    'release-assets.githubusercontent.com'))


class Redirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not allowed_url(newurl):
            raise UpdateError('Die Updatequelle verweist auf eine nicht erlaubte Adresse.')
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def fetch(url, limit):
    if not allowed_url(url):
        raise UpdateError('Ungültige Updatequelle.')
    req = urllib.request.Request(url, headers={'User-Agent': 'HomeLink-Updater', 'Accept': 'application/vnd.github+json'})
    try:
        with urllib.request.build_opener(Redirect).open(req, timeout=20) as response:
            body = response.read(limit+1)
            if len(body) > limit:
                raise UpdateError('Die heruntergeladene Datei ist zu groß.')
            return body
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError('GitHub-Repository oder veröffentlichte Updates nicht gefunden.') from error
        if error.code in (403, 429):
            raise UpdateError('GitHub lässt derzeit keine weitere Abfrage zu. Später erneut versuchen.') from error
        raise UpdateError('GitHub konnte nicht abgefragt werden.') from error
    except (urllib.error.URLError, TimeoutError) as error:
        raise UpdateError('Keine Verbindung zu GitHub. Eine Updatedatei kann weiterhin lokal hochgeladen werden.') from error


def release():
    cfg = config()
    if not cfg['repository']:
        raise UpdateError('Zuerst ein GitHub-Repository eintragen.')
    releases = json.loads(fetch('https://api.github.com/repos/'+cfg['repository']+'/releases?per_page=20', 1024*1024))
    if not isinstance(releases, list):
        raise UpdateError('Ungültige Antwort von GitHub.')
    for entry in releases:
        if entry.get('draft') or entry.get('prerelease') and not cfg['prereleases']:
            continue
        assets = [a for a in entry.get('assets', []) if isinstance(a.get('name'), str) and a['name'].endswith('.hlupdate')]
        if len(assets) != 1:
            continue
        asset = assets[0]
        url = asset.get('browser_download_url', '')
        parsed = urllib.parse.urlsplit(url)
        if not allowed_url(url) or parsed.hostname != 'github.com' or not parsed.path.lower().startswith(('/'+cfg['repository']+'/releases/download/').lower()):
            continue
        if type(asset.get('size')) is not int or not 0 < asset['size'] <= MAX_UPLOAD:
            continue
        return {'name': str(entry.get('name') or entry.get('tag_name', 'Update'))[:200],
                'tag': str(entry.get('tag_name', ''))[:100], 'changes': str(entry.get('body') or '')[:8000],
                'url': url, 'asset': asset['name'], 'prerelease': bool(entry.get('prerelease'))}
    raise UpdateError('Noch kein passendes HomeLink-Update veröffentlicht.')


def check_release():
    result = release()
    return {key: value for key, value in result.items() if key != 'url'}


def download(peer):
    with LOCK:
        ensure_idle()
        chosen = release()
        body = fetch(chosen['url'], MAX_UPLOAD)
        ROOT.mkdir(parents=True, exist_ok=True, mode=0o700)
        clean_old()
        folder = ROOT/secrets.token_hex(16); folder.mkdir(mode=0o700)
        try:
            (folder/'package.hlupdate').write_bytes(body)
            os.chmod(folder/'package.hlupdate', 0o600)
            return staged_bundle(folder/'package.hlupdate', peer)
        except BaseException:
            shutil.rmtree(folder)
            raise
