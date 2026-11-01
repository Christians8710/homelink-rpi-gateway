# HomeLink

**Zigbee & Homematic verbinden.**

HomeLink macht einen **Raspberry Pi 4 B** zum lokalen Netzwerk-Gateway für
einen **SONOFF ZBDongle-P** und ein **RPI-RF-MOD**. Zigbee2MQTT und OpenCCU
laufen auf deinem vorhandenen Server; der Pi stellt die Funkhardware bereit.

## Funktionen

- Zigbee über ser2net: `tcp://<Pi-IP>:8888`, Adapter `zstack`.
- Homematic/Homematic IP über HB-RF-ETH: UDP 3008.
- Deutsche Webverwaltung mit USB-Auswahl, Port-Einstellungen, Status und Logs.
- WLAN-Einrichtung mit eigenem Setup-AP und Rückkehr bei Verbindungsverlust.
- Individuelle Admin- und Setup-WLAN-Passwörter pro Installation.
- Signierte Updates über die Weboberfläche oder GitHub-Releases, mit Sicherung
  und automatischer Wiederherstellung bei Installationsfehlern.

## Unterstützte Basis

Raspberry Pi 4 B mit **Raspberry Pi OS Lite 32-bit, Debian 13/Trixie**, einer
MicroSD-Karte ab 16 GB (32 GB empfohlen), Stromversorgung und WLAN oder LAN.
Für Zigbee wird der SONOFF ZBDongle-P mit Coordinator-Firmware benötigt;
für Homematic das RPI-RF-MOD am GPIO-Anschluss. Du kannst auch nur eines der
beiden Funk-Gateways verwenden.

Die Pi-Anbindung verwendet den normalen PL011-UART und Linux-GPIO-v2.
Ein zusätzlicher piVCCU-/DKMS-Kerneltreiber ist nicht nötig.
64-Bit-Kernel mit 32-Bit-Benutzerland, wie beim getesteten Pi-OS-Image,
ist möglich. Ein 64-Bit-Benutzerland wird in dieser Version nicht unterstützt.

## Installation

1. Raspberry Pi OS Lite **32-bit** mit Raspberry Pi Imager schreiben.
2. Im Imager dein eigenes Benutzerkonto, SSH und zunächst dein Heim-WLAN
   einrichten; alternativ den Pi per LAN verbinden.
3. Per SSH auf dem Pi anmelden und ausführen:

```sh
sudo apt update
sudo apt install -y git
git clone https://github.com/Christians8710/homelink-rpi-gateway.git
cd homelink-rpi-gateway
sudo bash install.sh --country=DE
```

Der Installer prüft Board und Betriebssystem, installiert die benötigten
Pakete und baut die Funkbrücke nativ. Ist ser2net älter als 4.6.8, wird die
geprüfte Upstream-Version gebaut. Das kann einige Minuten dauern.

Danach `http://<Pi-IP>/admin` öffnen. Die neu erzeugten Zugangsdaten stehen
auf dem Pi in `/boot/firmware/HomeLink-Zugangsdaten.txt`:

```sh
sudo cat /boot/firmware/HomeLink-Zugangsdaten.txt
```

Die SSH-Anmeldung bleibt bei deinem eigenen Raspberry-Pi-OS-Konto.
Für den GPIO-UART den Pi einmal neu starten. Anschließend Homematic bei
Bedarf in der Webverwaltung aktivieren. Mit `--without-homematic` kann die
GPIO-/Bluetooth-Konfiguration bei einer reinen Zigbee-Installation entfallen.
Den Ländercode für dein Einsatzland anpassen.

## Zigbee2MQTT

```yaml
serial:
  port: tcp://<Pi-IP>:8888
  adapter: zstack
  baudrate: 115200
  rtscts: false
```

## OpenCCU

In OpenCCU unter **Einstellungen → Systemsteuerung → Erweiterte Einstellungen →
IP-Adresse (HB-RF-ETH)** die Pi-IP eintragen und OpenCCU neu starten.
HomeLink ist eine Funkbrücke und übernimmt keine Geräte, Programme oder
Schlüssel aus einer bestehenden CCU. Es betreibt selbst keine vollständige CCU.

## Updates

Unter **System → Updates** kann die `.hlupdate`-Datei aus einem
[GitHub-Release](https://github.com/Christians8710/homelink-rpi-gateway/releases)
hochgeladen werden. Die GitHub-Quelle ist bereits voreingestellt.
HomeLink prüft Signatur, Prüfsumme, Board und Version vor der Installation.
Der Bereich aktualisiert HomeLink; Betriebssystempakete werden separat mit
`apt` gepflegt. Weitere Informationen: [Betrieb und Updates](docs/operations.md).

## Projekt und Lizenzen

Die eigenständige HomeLink-Webverwaltung und die Installations-/Buildskripte
stehen unter MIT. Die abgeleitete Funkbrücke in `rfbridge/` steht unter
**CC BY-NC-SA 4.0**. Daher ist HomeLink als Gesamtpaket nicht uneingeschränkt
kommerziell nutzbar. Siehe [LICENSE](LICENSE), [NOTICE](NOTICE) und
[Lizenzübersicht](docs/licenses.md).

Die Funkbrücke basiert auf Alexander Reinerts
[HB-RF-ETH](https://github.com/alexreinert/HB-RF-ETH). ser2net wird separat unter
seiner GPL-Lizenz installiert. HomeLink ist eine unabhängige Anpassung.

## Prüfstand

Zigbee und Homematic wurden gemeinsam auf einem tatsächlichen Pi 4 B mit
SONOFF ZBDongle-P und RPI-RF-MOD betrieben und vom Projektbetreiber bestätigt.
Protokoll-, Update-, Signatur-, Wiederherstellungs- und Browserprüfungen wurden
zusätzlich durchgeführt. Die öffentliche Installation wurde außerdem in einer
getrennten Kopie des offiziellen Raspberry Pi OS Lite geprüft, einschließlich
ser2net 4.6.8, individueller Zugangsdaten und Erhalt vorhandener Einstellungen.
Der [Prüfbericht](docs/validation-1.0.0.json) beschreibt den Umfang.
Das ersetzt keinen Langzeit- oder Mehrgeräte-Hardwaretest.

## ❤️ Projekt unterstützen

Dieses Projekt wird kostenlos zur Verfügung gestellt und kontinuierlich weiterentwickelt.

Wenn dir die Software gefällt und du die Weiterentwicklung unterstützen möchtest, kannst du dies freiwillig über GitHub Sponsors tun.

❤️ [**Projekt über GitHub Sponsors unterstützen**](https://github.com/sponsors/Christians8710)

Deine Unterstützung hilft bei der Weiterentwicklung, Fehlerbehebung, Wartung und Umsetzung neuer Funktionen.

Vielen Dank für deine Unterstützung!

[Entwicklung und Release-Build](docs/development.md) · [Sicherheit](SECURITY.md)
