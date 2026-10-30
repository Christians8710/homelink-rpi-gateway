# Sicherheit

HomeLink ist für ein vertrauenswürdiges lokales Netz vorgesehen. Die
Webverwaltung nutzt HTTP; das HB-RF-ETH-Protokoll und die Zigbee-TCP-Brücke
bieten selbst keine Benutzeranmeldung oder Transportverschlüsselung.
Keine dieser Schnittstellen für öffentliche Internetzugriffe freigeben.

Admin-Passwörter werden mit scrypt gehasht. Sitzungen sind zeitlich begrenzt,
IP-gebunden und werden bei einer Passwortänderung ungültig. Schreibzugriffe
prüfen Anmeldung, Origin und CSRF-Token. Updates erfordern zusätzlich eine
gültige Ed25519-Signatur und passende Paketmetadaten.

Bitte keine Passwörter, privaten Schlüssel, CCU-Backups, vollständigen
Zugangsdaten-Dateien oder unbereinigten Logs in öffentlichen Issues posten.
Ein Sicherheitsproblem zunächst knapp ohne ausnutzbare Details melden und
um einen privaten Austausch bitten.
