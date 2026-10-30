# Betrieb und Updates

Die Webverwaltung läuft unter `http://<Pi-IP>/admin`. Sie verwaltet eine
serielle Zigbee-Verbindung und eine Homematic-Funkbrücke. Funkdienste laufen
unabhängig vom Webserver.

## WLAN

Eine schon aktive WLAN-Verbindung wird beim Start der Webverwaltung erhalten.
Ohne erreichbare bekannte Verbindung öffnet HomeLink sein individuelles
Setup-WLAN. SSID und Passwort stehen in der lokalen Zugangsdaten-Datei.
Im Setup-Netz ist die WLAN-Einrichtung unter `http://192.168.4.1` erreichbar.
Die HomeLink-Webseite muss gegebenenfalls manuell im Browser geöffnet werden.

## Versionsfolge

Die erste öffentliche Version ist 1.0.0. Frühere interne Entwicklungsstände
einschließlich 1.0-r1 bleiben regulär aktualisierbar. Die interne Debian-
Versionskennung beginnt mit `1:`, damit die Umstellung von den früheren
internen 1.2.x-Versionen korrekt eingeordnet wird. Diese Kennung bleibt bei
weiteren Releases erhalten.

## Webupdates

Ein `.hlupdate`-Paket enthält das Debian-Paket, ein Manifest und eine
Ed25519-Signatur. Nur Pakete mit dem hinterlegten Projektschlüssel werden
akzeptiert. Eine Installation benötigt die Admin-Anmeldung und wird bewusst
gestartet. Das Hochladen allein installiert nichts.

Der Updateprozess läuft als eigener systemd-Dienst. Er sichert vorher die
Software und die HomeLink-/WLAN-Konfiguration. Bei einem Installationsfehler
wird die vorherige Paketversion installiert und die Konfiguration restauriert.
Ein Stromausfall oder Hardware-/Dateisystemschaden ist damit nicht abgedeckt.

- Sicherungen: `/var/backups/homelink/`
- Softwarecache: `/var/cache/homelink/packages/`
- Updatevorgänge: `/var/lib/homelink/updates/`
- Setup-Konfiguration: `/etc/zigbee-gateway/`
- Öffentlicher Prüfschlüssel: `/usr/share/homelink/update-public-key.pem`

Die internen Pfade und Paket-/Dienstnamen enthalten teilweise noch
`zigbee-gateway`. Sie bleiben zur Kompatibilität mit den Entwicklungsständen
erhalten. Der Produktname in der Oberfläche ist HomeLink.

## Dienste

```sh
systemctl status ser2net zigbee-setup rfbridge
journalctl -u ser2net -u rfbridge --no-pager -n 80
```

Bei einem HomeLink-Webupdate werden Zigbee und Homematic nur dann neu
gestartet, wenn ein signiertes Release dies ausdrücklich vorsieht.
Firmware des SONOFF-Dongles und des RPI-RF-MOD wird von HomeLink nicht geflasht.

## Betriebssystem und ser2net

Raspberry Pi OS und dessen Pakete werden getrennt per SSH aktualisiert:

```sh
sudo apt update
sudo apt full-upgrade
sudo reboot
```

Der selbst gebaute ser2net-Stand ist ebenfalls als Debian-Paket registriert.
Ein neueres Paket aus den Paketquellen kann ihn regulär ersetzen. Neue
Upstream-Versionen, die dort noch fehlen, benötigen einen aktualisierten,
geprüften Projekt-Build. Der aktuelle Installer baut bei Bedarf ser2net 4.6.8.
