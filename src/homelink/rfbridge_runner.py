#!/usr/bin/python3
# SPDX-License-Identifier: MIT
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, '/opt/zigbee-gateway')
import gateway_rf

config = json.loads(Path('/etc/zigbee-gateway/rfbridge.json').read_text())
if not config['enabled'] or not gateway_rf.ready():
    print('RFBridge wartet auf Aktivierung bzw. den vorbereiteten GPIO-UART nach dem Neustart.')
    raise SystemExit(1 if '--ready' in sys.argv else 0)
if '--ready' in sys.argv:
    raise SystemExit(0)
args = ['/usr/local/sbin/rfbridge-pi', '--device', '/dev/serial0']
if config.get('ccu_ip'):
    args += ['--allow', config['ccu_ip']]
os.execv(args[0], args)
