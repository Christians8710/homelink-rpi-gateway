# SPDX-License-Identifier: MIT
"""Authenticated RFBridge administration; called only by gateway_admin."""
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import threading
import time

CONFIG = Path('/etc/zigbee-gateway/rfbridge.json')
STATUS = Path('/run/rfbridge/status.json')
LOCK = threading.Lock()


def command(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=20)


def ready():
    device = Path('/dev/serial0')
    try:
        console = any(word.startswith(('console=serial0,', 'console=ttyAMA0,'))
                      for word in Path('/proc/cmdline').read_text().split())
        return device.exists() and device.resolve().name == 'ttyAMA0' and not console
    except OSError:
        return False


def status():
    config = json.loads(CONFIG.read_text())
    service = command(['systemctl', 'is-active', 'rfbridge']).stdout.strip()
    uart_ready = ready()
    live = {}
    if service == 'active':
        try:
            live = json.loads(STATUS.read_text())
            if time.monotonic() * 1000 - live['monotonic_ms'] > 5000:
                live = {}
        except (OSError, ValueError, KeyError):
            pass
    if not uart_ready:
        message = 'GPIO-UART noch nicht aktiv. Neustart erforderlich.'
    elif not config['enabled']:
        message = 'Homematic-Funkbrücke ist deaktiviert.'
    elif service != 'active':
        message = 'Funkbrücke ist nicht aktiv. Protokoll prüfen.'
    elif live.get('peer'):
        message = 'OpenCCU verbunden. Die Erkennung und Funkfunktion in OpenCCU prüfen.'
    else:
        message = 'UART bereit. Warte auf die Verbindung der OpenCCU.'
    return {'config': config, 'uart_ready': uart_ready, 'service': service,
            'message': message, 'live': live, 'port': 3008, 'protocol': 'HB-RF-ETH / UDP',
            'experimental': True, 'radio_detection': 'in OpenCCU prüfen'}


def save(data):
    enabled, peer = data.get('enabled'), data.get('ccu_ip', '')
    if type(enabled) is not bool or not isinstance(peer, str):
        raise ValueError('Ungültige RFBridge-Einstellung.')
    peer = peer.strip()
    if peer:
        address = ipaddress.IPv4Address(peer)
        private = any(address in net for net in (ipaddress.ip_network('10.0.0.0/8'),
                      ipaddress.ip_network('172.16.0.0/12'), ipaddress.ip_network('192.168.0.0/16')))
        if not private or address in ipaddress.ip_network('192.168.4.0/24'):
            raise ValueError('Eine private OpenCCU-Adresse außerhalb des Einrichtungs-WLANs verwenden.')
    config = {'enabled': enabled, 'ccu_ip': peer}
    with LOCK:
        previous = CONFIG.read_text()
        temp = CONFIG.with_name('rfbridge.' + secrets.token_hex(6) + '.tmp')
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(json.dumps(config, indent=2) + '\n')
            stream.flush(); os.fsync(stream.fileno())
        temp.replace(CONFIG)
        try:
            action = 'restart' if enabled and ready() else 'stop'
            result = command(['systemctl', action, 'rfbridge'])
            if result.returncode:
                raise RuntimeError('Funkbrücke konnte nicht umgeschaltet werden.')
        except Exception:
            CONFIG.write_text(previous)
            raise
    return {'message': 'Gespeichert. ' + status()['message']}


def log():
    return {'log': command(['journalctl', '-u', 'rfbridge', '-n', '60', '--no-pager', '-o', 'short-iso']).stdout}


def restart():
    if not ready():
        return {'message': 'GPIO-UART noch nicht aktiv. Neustart erforderlich.'}
    if not json.loads(CONFIG.read_text())['enabled']:
        return {'message': 'Zuerst die Homematic-Funkbrücke aktivieren und speichern.'}
    if command(['systemctl', 'restart', 'rfbridge']).returncode:
        raise RuntimeError('Neustart der Funkbrücke fehlgeschlagen.')
    return {'message': 'Homematic-Funkbrücke neu gestartet. OpenCCU bei Bedarf ebenfalls neu starten.'}
