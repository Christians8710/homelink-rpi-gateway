#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Local, authenticated administration for the serial gateway (stdlib only)."""
import hashlib
import grp
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import subprocess
import threading
import time
import gateway_rf
import gateway_updates
from urllib.parse import urlsplit, parse_qs

BASE = Path('/etc/zigbee-gateway')
AUTH = BASE / 'admin.json'
SERIAL = BASE / 'serial.json'
YAML = BASE / 'ser2net.yaml'
PAGE = Path('/opt/zigbee-gateway/admin.html')
SESSION_DIR = Path('/run/homelink-admin')
SESSIONS = {}
ATTEMPTS = {}
GUARD = threading.RLock()
SESSION_AGE = 8 * 3600
IDLE_AGE = 30 * 60
BAUDRATES = (9600, 19200, 38400, 57600, 115200, 230400, 460800)


def run(args, timeout=12):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                          env=dict(os.environ, LC_ALL='C.UTF-8'))


def atomic(path, data):
    tmp = path.with_name(path.name + '.' + secrets.token_hex(6) + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        if path == YAML:
            os.fchown(fd, 0, grp.getgrnam('dialout').gr_gid)
            os.fchmod(fd, 0o640)
        with os.fdopen(fd, 'w') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def password_record(password):
    salt = secrets.token_hex(16)
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
    return {'username': 'admin', 'salt': salt, 'hash': key.hex()}


def password_matches(password, record):
    key = hashlib.scrypt(password.encode(), salt=bytes.fromhex(record['salt']), n=16384, r=8, p=1)
    return hmac.compare_digest(key.hex(), record['hash'])


def session_file(sid):
    return SESSION_DIR/(hashlib.sha256(sid.encode()).hexdigest()+'.json')


def store_session(sid, session):
    SESSION_DIR.mkdir(mode=0o700, exist_ok=True)
    session['auth_stamp'] = hashlib.sha256(AUTH.read_bytes()).hexdigest()
    atomic(session_file(sid), json.dumps(session))


def remove_session(sid):
    if sid:
        session_file(sid).unlink(missing_ok=True)


def clear_sessions():
    SESSIONS.clear()
    if SESSION_DIR.exists():
        for path in SESSION_DIR.glob('*.json'):
            if re.fullmatch(r'[a-f0-9]{64}\.json', path.name):
                path.unlink(missing_ok=True)


def serial_devices():
    result = []
    by_id = Path('/dev/serial/by-id')
    links = sorted(by_id.iterdir()) if by_id.is_dir() else []
    used = set()
    for item in links + sorted(Path('/dev').glob('ttyUSB*')) + sorted(Path('/dev').glob('ttyACM*')):
        try:
            real = item.resolve(strict=True)
            info = real.stat()
            if not stat.S_ISCHR(info.st_mode) or os.major(info.st_rdev) not in (166, 188):
                continue
            if str(real) in used:
                continue
            used.add(str(real))
            result.append({'path': str(item), 'device': str(real), 'name': item.name,
                           'stable': str(item).startswith('/dev/serial/by-id/')})
        except OSError:
            continue
    return result


def validate_config(data):
    if not isinstance(data, dict):
        raise ValueError('Ungültige Einstellungen.')
    device = data.get('device')
    if not isinstance(device, str) or device not in {d['path'] for d in serial_devices()}:
        raise ValueError('Bitte einen aktuell erkannten USB-Seriell-Adapter auswählen.')
    port, baud, flow = data.get('port'), data.get('baudrate'), data.get('rtscts')
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError('Der TCP-Port muss zwischen 1024 und 65535 liegen.')
    if port in (1883, 5353):
        raise ValueError('Dieser Port ist für einen anderen Netzwerkdienst reserviert.')
    if type(baud) is not int or baud not in BAUDRATES or type(flow) is not bool:
        raise ValueError('Baudrate oder Flusskontrolle ungültig.')
    return {'device': device, 'port': port, 'baudrate': baud, 'rtscts': flow}


def read_config():
    return json.loads(SERIAL.read_text())


def render_config(data):
    # Quote the entire connector as a YAML string; input never becomes shell syntax.
    connector = 'serialdev,' + data['device'] + ',' + str(data['baudrate']) + 'n81,local'
    if data['rtscts']:
        connector += ',rtscts'
    return ('%YAML 1.1\n---\ndefault:\n  name: mdns\n  value: false\n'
            'connection: &zigbee\n  accepter: tcp,' + str(data['port']) +
            '\n  enable: on\n  options:\n    kickolduser: true\n  connector: ' +
            json.dumps(connector, ensure_ascii=True) + '\n')


def restart_service():
    result = run(['systemctl', 'restart', 'ser2net'], timeout=25)
    if result.returncode:
        raise RuntimeError('ser2net konnte nicht neu gestartet werden.')
    time.sleep(0.6)
    if run(['systemctl', 'is-active', '--quiet', 'ser2net']).returncode:
        raise RuntimeError('ser2net ist nach dem Neustart nicht aktiv.')


def port_listening(port):
    # /proc avoids a test TCP client which would displace Zigbee2MQTT (kickolduser).
    for name in ('/proc/net/tcp', '/proc/net/tcp6'):
        try:
            for line in Path(name).read_text().splitlines()[1:]:
                fields = line.split()
                if fields[3] == '0A' and int(fields[1].split(':')[1], 16) == port:
                    return True
        except OSError:
            pass
    return False


def apply_config(data):
    config = validate_config(data)
    with GUARD:
        old_yaml, old_serial = YAML.read_text(), SERIAL.read_text()
        atomic(YAML, render_config(config))
        try:
            restart_service()
            for _ in range(12):
                if port_listening(config['port']):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('Der gewünschte TCP-Port konnte nicht geöffnet werden.')
            atomic(SERIAL, json.dumps(config, indent=2) + '\n')
        except Exception:
            atomic(YAML, old_yaml)
            atomic(SERIAL, old_serial)
            restart_service()
            raise
    return config


def local_request(handler):
    try:
        peer = ipaddress.ip_address(handler.client_address[0])
        if not (peer.is_private or peer.is_loopback):
            return False
        host = urlsplit('http://' + handler.headers.get('Host', ''))
        if host.username or host.password or host.port not in (None, 80):
            return False
        hostname = host.hostname or ''
        machine = socket.gethostname().lower()
        return hostname in (handler.connection.getsockname()[0], machine, machine+'.local',
                            'zigbee-gateway', 'zigbee-gateway.local', 'localhost')
    except (ValueError, OSError):
        return False


def same_origin(handler):
    return handler.headers.get('Origin') == 'http://' + handler.headers.get('Host', '')


def send(handler, data, code=200, html=False, cookie=None):
    body = data.encode() if html else json.dumps(data, ensure_ascii=False).encode()
    handler.send_response(code)
    handler.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json; charset=utf-8')
    handler.send_header('Content-Length', str(len(body)))
    handler.send_header('Cache-Control', 'no-store')
    handler.send_header('X-Frame-Options', 'DENY')
    handler.send_header('X-Content-Type-Options', 'nosniff')
    handler.send_header('Referrer-Policy', 'no-referrer')
    handler.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
    if cookie:
        handler.send_header('Set-Cookie', cookie)
    handler.end_headers()
    handler.wfile.write(body)


def json_body(handler):
    if handler.headers.get('Content-Type', '').split(';')[0] != 'application/json':
        raise ValueError('JSON-Anfrage erwartet.')
    if handler.headers.get('Transfer-Encoding'):
        raise ValueError('Ungültige Anfrage.')
    length = int(handler.headers.get('Content-Length', '0'))
    if not 0 < length <= 4096:
        raise ValueError('Ungültige Anfragegröße.')
    data = json.loads(handler.rfile.read(length))
    if not isinstance(data, dict):
        raise ValueError('Ungültige Anfrage.')
    return data


def current_session(handler):
    raw = handler.headers.get('Cookie', '')
    sid = next((part.strip().split('=', 1)[1] for part in raw.split(';')
                if part.strip().startswith('gateway_admin=')), '')
    now = time.monotonic()
    with GUARD:
        if not re.fullmatch(r'[A-Za-z0-9_-]{43}', sid):
            return '', None
        if sid not in SESSIONS:
            try:
                SESSIONS[sid] = json.loads(session_file(sid).read_text())
            except (OSError, ValueError):
                pass
        for key in list(SESSIONS):
            value = SESSIONS[key]
            if (now - value['created'] > SESSION_AGE or now - value['seen'] > IDLE_AGE
                    or value.get('auth_stamp') != hashlib.sha256(AUTH.read_bytes()).hexdigest()):
                SESSIONS.pop(key, None)
                remove_session(key)
        session = SESSIONS.get(sid)
        if session and session['ip'] == handler.client_address[0]:
            session['seen'] = now
            store_session(sid, session)
            return sid, session
    return '', None


def status():
    config = read_config()
    active = run(['systemctl', 'is-active', 'ser2net']).stdout.strip()
    package = run(['dpkg-query', '-W', '-f=${Version}', 'ser2net']).stdout.strip()
    version_path = Path('/usr/share/zigbee-gateway/admin-version')
    return {'hostname': socket.gethostname(), 'service': active,
            'ser2net_version': package, 'admin_version': version_path.read_text().strip(),
            'config': config, 'listening': port_listening(config['port']),
            'device_present': Path(config['device']).exists()}


def handle(handler):
    if not local_request(handler):
        return send(handler, {'error': 'Zugriff nur über die lokale Gateway-Adresse erlaubt.'}, 403)
    path = urlsplit(handler.path).path
    if handler.command == 'GET' and path in ('/admin', '/admin/'):
        return send(handler, PAGE.read_text(), html=True)
    if handler.command == 'GET' and path == '/admin/licenses':
        return send(handler, Path('/opt/zigbee-gateway/licenses.html').read_text(), html=True)
    if handler.command == 'GET' and path == '/admin/api/update/progress':
        try:
            identifier = parse_qs(urlsplit(handler.path).query).get('id', [''])[0]
            result = gateway_updates.progress(identifier, handler.headers.get('X-Update-Token', ''), handler.client_address[0])
            return send(handler, result)
        except (ValueError, OSError, KeyError):
            return send(handler, {'error': 'Updatevorgang nicht verfügbar.'}, 403)
    sid, session = current_session(handler)
    if handler.command == 'POST' and not same_origin(handler):
        return send(handler, {'error': 'Ursprung der Anfrage ungültig.'}, 403)
    try:
        if handler.command == 'POST' and path == '/admin/api/login':
            data = json_body(handler)
            user, password = data.get('username', ''), data.get('password', '')
            if not isinstance(user, str) or not isinstance(password, str) or len(password) > 256:
                raise ValueError('Ungültige Anmeldung.')
            peer, now = handler.client_address[0], time.monotonic()
            with GUARD:
                ATTEMPTS.update({p: [t for t in times if now - t < 300] for p, times in ATTEMPTS.items()})
                ATTEMPTS[peer] = ATTEMPTS.get(peer, [])
                if len(ATTEMPTS[peer]) >= 8:
                    return send(handler, {'error': 'Zu viele Versuche. Bitte fünf Minuten warten.'}, 429)
                record = json.loads(AUTH.read_text())
                valid = password_matches(password, record)
                if not valid or not hmac.compare_digest(user, record['username']):
                    ATTEMPTS[peer].append(now)
                    return send(handler, {'error': 'Benutzername oder Passwort falsch.'}, 401)
                ATTEMPTS.pop(peer, None)
                if sid:
                    SESSIONS.pop(sid, None)
                    remove_session(sid)
                # Bound memory even if a browser repeatedly authenticates without cookies.
                if len(SESSIONS) >= 64:
                    old = next(iter(SESSIONS)); SESSIONS.pop(old); remove_session(old)
                sid = secrets.token_urlsafe(32)
                session = {'csrf': secrets.token_urlsafe(32), 'created': now, 'seen': now, 'ip': peer}
                SESSIONS[sid] = session
                store_session(sid, session)
            return send(handler, {'csrf': session['csrf']}, cookie='gateway_admin=' + sid + '; Path=/admin; HttpOnly; SameSite=Strict; Max-Age=28800')
        if not session:
            return send(handler, {'error': 'Bitte zuerst anmelden.'}, 401)
        if handler.command == 'GET':
            if path == '/admin/api/session':
                return send(handler, {'csrf': session['csrf']})
            if path == '/admin/api/status':
                return send(handler, status())
            if path == '/admin/api/devices':
                usb = run(['lsusb']).stdout.strip()
                return send(handler, {'devices': serial_devices(), 'usb': usb})
            if path == '/admin/api/log':
                log = run(['journalctl', '-u', 'ser2net', '-n', '60', '--no-pager', '-o', 'short-iso']).stdout
                return send(handler, {'log': log})
            if path == '/admin/api/rf/status':
                return send(handler, gateway_rf.status())
            if path == '/admin/api/rf/log':
                return send(handler, gateway_rf.log())
            if path == '/admin/api/update/state':
                return send(handler, gateway_updates.state())
        if handler.command == 'POST':
            if not hmac.compare_digest(handler.headers.get('X-Admin-Token', ''), session['csrf']):
                return send(handler, {'error': 'Sitzung ungültig. Bitte neu anmelden.'}, 403)
            if path == '/admin/api/update/upload':
                return send(handler, gateway_updates.upload(handler))
            data = json_body(handler)
            if path == '/admin/api/update/install':
                return send(handler, gateway_updates.start(data.get('id')), 202)
            if path == '/admin/api/update/config':
                return send(handler, gateway_updates.save_config(data))
            if path == '/admin/api/update/check':
                return send(handler, gateway_updates.check_release())
            if path == '/admin/api/update/download':
                return send(handler, gateway_updates.download(handler.client_address[0]))
            if path == '/admin/api/rf/config':
                return send(handler, gateway_rf.save(data))
            if path == '/admin/api/rf/restart':
                return send(handler, gateway_rf.restart())
            if path == '/admin/api/config':
                config = apply_config(data)
                return send(handler, {'message': 'Gespeichert. ser2net wurde neu gestartet.', 'config': config})
            if path == '/admin/api/restart':
                with GUARD:
                    restart_service()
                return send(handler, {'message': 'ser2net wurde neu gestartet.'})
            if path == '/admin/api/logout':
                with GUARD:
                    SESSIONS.pop(sid, None)
                    remove_session(sid)
                return send(handler, {'message': 'Abgemeldet.'}, cookie='gateway_admin=; Path=/admin; HttpOnly; SameSite=Strict; Max-Age=0')
            if path == '/admin/api/password':
                old, new = data.get('current', ''), data.get('password', '')
                if not isinstance(old, str) or not isinstance(new, str) or not 12 <= len(new) <= 128 or len(old) > 256:
                    raise ValueError('Das neue Passwort muss 12 bis 128 Zeichen haben.')
                with GUARD:
                    record = json.loads(AUTH.read_text())
                    if not password_matches(old, record):
                        return send(handler, {'error': 'Bisheriges Admin-Passwort falsch.'}, 403)
                    atomic(AUTH, json.dumps(password_record(new)) + '\n')
                    clear_sessions()
                return send(handler, {'message': 'Passwort geändert. Bitte neu anmelden.'}, cookie='gateway_admin=; Path=/admin; HttpOnly; SameSite=Strict; Max-Age=0')
        return send(handler, {'error': 'Nicht gefunden.'}, 404)
    except gateway_updates.UpdateError as error:
        return send(handler, {'error': str(error)}, 400)
    except (ValueError, KeyError, TypeError):
        return send(handler, {'error': 'Eingaben prüfen: erkannten USB-Adapter, TCP-Port und Baudrate auswählen.'}, 400)
    except subprocess.TimeoutExpired:
        return send(handler, {'error': 'Zeitüberschreitung beim Dienstzugriff. Status und Protokoll prüfen.'}, 503)
    except Exception:
        return send(handler, {'error': 'Aktion fehlgeschlagen. Bisherige Einstellungen wurden soweit möglich wiederhergestellt. Bitte Protokoll prüfen.'}, 503)
