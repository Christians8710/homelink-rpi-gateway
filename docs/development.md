# Entwicklung und Releases

## Lokaler Build auf dem Pi

```sh
python3 tools/build.py --output dist
```

Der Build benötigt einen nativen ARM-Compiler und erzeugt ein Debian-Paket.
Alternativ kann ein auf dem Pi gebautes ARMHF-Programm mit
`--rfbridge-binary /pfad/rfbridge-pi` angegeben werden. Der Build prüft dessen
ELF-Architektur. `rfbridge/` muss bei Änderungen neu kompiliert werden.

Die Version steht in `VERSION`, die Debian-Version in `PACKAGE_VERSION`.
Für stabile Versionen beispielsweise `1.0.1` und `1:1.0.1-1`, für weitere
Teststände beispielsweise `1.1-r1` und `1:1.1~r1-1` verwenden.

## Signieren

Der private Projektschlüssel bleibt außerhalb dieses Repositorys. Er wird
nicht in Images, Pakete, GitHub-Releases oder GitHub Actions hochgeladen.

```sh
python3 tools/build.py --output dist --signing-key /privater/pfad/signing-key.pem
```

Der Build prüft, dass der private Schlüssel zum veröffentlichten Schlüssel
unter `assets/update-public-key.pem` gehört. Ohne passenden privaten Schlüssel
kann jeder den Quellcode bauen, aber keine offiziellen Updates signieren.

Ein Release enthält genau eine `.hlupdate`-Datei, die zugehörige SHA-256-Datei,
das Debian-Paket sowie den vollständigen Quellcode als Archiv. Der Quellcode
und die Dokumentation werden im Repository versioniert. GitHub Actions prüft
Python, die Protokolltests und die Konfigurationslogik; es veröffentlicht keine
unsignierten Updates automatisch.

## Prüfungen

```sh
python3 -m unittest discover -s tests -v
cc -O2 -Wall -Wextra -Werror rfbridge/test_protocol.c rfbridge/protocol.c -o /tmp/homelink-protocol-test
/tmp/homelink-protocol-test
```

`install.sh --no-start` ist für getrennte Image-/Installationsprüfungen gedacht.
Die Dienste werden dabei eingerichtet, aber nicht gestartet. apt-Dienste sind
in einer Test-Chroot zusätzlich mit einer `policy-rc.d` zu unterbinden.
