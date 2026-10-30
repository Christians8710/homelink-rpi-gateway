# SPDX-License-Identifier: MIT
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import os
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src/homelink'))
spec=importlib.util.spec_from_file_location('configure_instance',ROOT/'tools/configure_instance.py')
settings=importlib.util.module_from_spec(spec);spec.loader.exec_module(settings)


def fresh_root(root):
    boot=root/'boot/firmware';boot.mkdir(parents=True)
    (boot/'config.txt').write_text('[all]\n')
    (boot/'cmdline.txt').write_text('console=serial0,115200 console=tty1 root=PARTUUID=test-02 rootwait\n')
    network=root/'etc/NetworkManager/system-connections';network.mkdir(parents=True)
    (network/'my-home.nmconnection').write_text('PRIVATE NETWORK PROFILE\n')
    ssh=root/'etc/ssh';ssh.mkdir(parents=True)
    (ssh/'sshd_config').write_text('ORIGINAL SSH SETTINGS\n')
    return boot,network,ssh


class Configuration(unittest.TestCase):
    def test_unique_credentials_and_existing_settings_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);fresh_root(root)
            first=settings.configure(root)
            before={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(settings.configure(root),{})
            after={str(p.relative_to(root)):p.read_bytes() for p in root.rglob('*') if p.is_file()}
            self.assertEqual(before,after)
            with tempfile.TemporaryDirectory() as other:
                second_root=Path(other);fresh_root(second_root);second=settings.configure(second_root)
            self.assertNotEqual(first['admin_password'],second['admin_password'])
            self.assertNotEqual(first['wifi_password'],second['wifi_password'])
            self.assertEqual((root/'etc/ssh/sshd_config').read_text(),'ORIGINAL SSH SETTINGS\n')
            self.assertEqual((root/'etc/NetworkManager/system-connections/my-home.nmconnection').read_text(),'PRIVATE NETWORK PROFILE\n')
            self.assertNotIn('console=serial0',(root/'boot/firmware/cmdline.txt').read_text())
            self.assertIn('root=PARTUUID=test-02',(root/'boot/firmware/cmdline.txt').read_text())

    def test_existing_wifi_is_not_reconnected(self):
        with tempfile.TemporaryDirectory() as temp:
            setup=Path(temp)/'setup.json';setup.write_text(json.dumps({'ssid':'Setup-test','password':'test-only-passphrase'}))
            with patch.dict(os.environ,{'HOMELINK_SETUP_CONFIG':str(setup)}):
                spec=importlib.util.spec_from_file_location('portal_for_test',ROOT/'src/homelink/portal.py')
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            calls=[]
            def fake_nm(*args):
                calls.append(args)
                if 'GENERAL.CONNECTION' in args:return type('Result',(),{'stdout':'my-existing-wifi\n','returncode':0})()
                if 'GENERAL.STATE' in args:return type('Result',(),{'stdout':'100 (connected)\n','returncode':0})()
                return type('Result',(),{'stdout':'','returncode':0})()
            module.nm=fake_nm
            module.initial_network('wlan0')
            self.assertEqual(module.STATE['mode'],'home')
            self.assertFalse(any(args[:2]==('connection','up') for args in calls))


if __name__=='__main__':unittest.main()
