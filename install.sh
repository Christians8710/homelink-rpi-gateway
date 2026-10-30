#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Start with sudo bash install.sh" >&2; exit 1; }
project=$(cd -- "$(dirname -- "$0")" && pwd)
country=DE
activate=1
gpio=1
for arg in "$@"; do
 case "$arg" in
  --country=*) country=${arg#*=} ;;
  --no-start) activate=0 ;;
  --without-homematic) gpio=0 ;;
  *) echo "Unknown option: $arg" >&2; exit 1 ;;
 esac
done
[[ $country =~ ^[A-Z]{2}$ ]] || { echo 'Invalid country code' >&2; exit 1; }
[[ $(dpkg --print-architecture) == armhf ]] || { echo 'Raspberry Pi OS Lite 32-bit (armhf) is required.' >&2; exit 1; }
grep -aq '^Raspberry Pi 4 Model B' /proc/device-tree/model || { echo 'Raspberry Pi 4 B required.' >&2; exit 1; }
. /etc/os-release
[[ ${VERSION_CODENAME:-} == trixie ]] || { echo 'Raspberry Pi OS Trixie required.' >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends python3 network-manager ser2net dnsmasq-base nftables iw rfkill openssl usbutils psmisc avahi-daemon build-essential autoconf automake libtool pkg-config libyaml-dev libgensio-dev libcrypt-dev libpam0g-dev curl ca-certificates
ser_version=$(dpkg-query -W -f='${Version}' ser2net)
if dpkg --compare-versions "$ser_version" lt 4.6.8; then bash "$project/tools/build_ser2net.sh"; fi
if ! id ser2net >/dev/null 2>&1; then useradd -r -M -s /usr/sbin/nologin -g dialout ser2net; fi
build_dir=$(mktemp -d /var/tmp/homelink-build.XXXXXX)
cleanup() { [[ "$build_dir" == /var/tmp/homelink-build.* ]] && rm -rf -- "$build_dir"; }
trap cleanup EXIT
python3 "$project/tools/build.py" --output "$build_dir"
config_args=(--country "$country")
[[ $gpio == 1 ]] || config_args+=(--without-gpio)
python3 "$project/tools/configure_instance.py" "${config_args[@]}"
dpkg --force-confold -i "$build_dir"/HomeLink-*_armhf.deb
python3 "$project/tools/cache_package.py" "$build_dir"/HomeLink-*_armhf.deb
systemctl daemon-reload
systemctl enable NetworkManager ser2net zigbee-setup rfbridge
if [[ $gpio == 1 ]]; then systemctl disable hciuart.service 2>/dev/null || true; fi
if [[ $activate == 1 ]]; then
 udevadm control --reload-rules
 systemctl restart ser2net zigbee-setup
 echo 'HomeLink installed. Open http://<Pi-IP>/admin'
 echo 'Credentials are stored in /boot/firmware/HomeLink-Zugangsdaten.txt'
 echo 'Reboot once to activate the GPIO UART, then enable Homematic in the web interface.'
else
 echo 'HomeLink installed; services were not started (--no-start).'
fi
