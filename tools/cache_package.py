#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import hashlib
from pathlib import Path
import os
import shutil
import subprocess
import sys
package=Path(sys.argv[1])
version=subprocess.check_output(['dpkg-deb','-f',str(package),'Version'],text=True).strip()
folder=Path('/var/cache/homelink/packages');folder.mkdir(parents=True,exist_ok=True,mode=0o700)
destination=folder/(hashlib.sha256(version.encode()).hexdigest()[:24]+'.deb')
shutil.copyfile(package,destination);os.chmod(destination,0o600)
