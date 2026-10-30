#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
script_dir=$(cd -- "$(dirname -- "$0")" && pwd)
task_dir=$(mktemp -d /var/tmp/homelink-ser2net.XXXXXX)
cleanup() { [[ "$task_dir" == /var/tmp/homelink-ser2net.* ]] && rm -rf -- "$task_dir"; }
trap cleanup EXIT
cd "$task_dir"
curl --fail --location --retry 3 --proto '=https' --tlsv1.2 'https://codeload.github.com/cminyard/ser2net/tar.gz/refs/tags/v4.6.8' -o source.tar.gz
printf '%s  source.tar.gz\n' '066790a7ed75534808a139d69840a89647e355b887be383b4b1704e2305601a8' | sha256sum -c -
tar -xzf source.tar.gz
cd ser2net-4.6.8
autoreconf -fi
./configure --prefix=/usr --sysconfdir=/etc --with-pam=yes
make -j2
cd "$task_dir"
mkdir base
cd base
apt-get download ser2net
dpkg-deb -R ser2net_*_armhf.deb ../package
cd "$task_dir"
install -m 755 ser2net-4.6.8/ser2net package/usr/sbin/ser2net
strip package/usr/sbin/ser2net
sed -i 's/^Version:.*/Version: 4.6.8-0homelink1/' package/DEBIAN/control
sed -i '/^Depends:/s/$/, libcrypt1/' package/DEBIAN/control
cp ser2net-4.6.8/COPYING package/usr/share/doc/ser2net/COPYING.upstream
cp source.tar.gz package/usr/share/doc/ser2net/ser2net-4.6.8-source.tar.gz
cp "$script_dir/build_ser2net.sh" package/usr/share/doc/ser2net/build-homelink.sh
(cd package; find usr -type f -print0 | LC_ALL=C sort -z | xargs -0 md5sum > DEBIAN/md5sums)
dpkg-deb --root-owner-group -b package ser2net_4.6.8-0homelink1_armhf.deb
dpkg --force-confold -i ser2net_4.6.8-0homelink1_armhf.deb
