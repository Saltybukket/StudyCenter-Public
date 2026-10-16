# Entwicklung – aus Git klonen und starten

**Du willst StudyCenter nur benutzen?** Nimm lieber den [fertigen Windows-Installer oder das Linux-Paket](../README.md#️-jetzt-herunterladen--beta2). Dieser Abschnitt ist für Entwickler und alle, die Änderungen am Quellcode ausprobieren möchten.

## Windows (PowerShell oder Eingabeaufforderung)

Git muss für das Klonen vorhanden sein. Python, pip und Tesseract musst du nicht vorinstallieren.

```powershell
git clone https://github.com/Saltybukket/StudyCenter-Public.git
cd StudyCenter-Public
.\start.cmd
```

## Linux (Ubuntu 24.04 x64)

```sh
git clone https://github.com/Saltybukket/StudyCenter-Public.git
cd StudyCenter-Public
sh start.sh
```

Der erste Start benötigt Internetzugang und freien Speicher. Das Skript richtet die Entwicklungsumgebung ein und startet die lokale Anwendung. Spätere Starts verwenden die bereits vorbereiteten Komponenten.

**Was passiert beim ersten Start?** Der Starter lädt das festgelegte `uv`-Werkzeug (0.8.22), prüft dessen SHA-256, verwendet die projektlokale Python-Version 3.12.11 und richtet die Abhängigkeiten sowie portable Browser-/OCR-Komponenten ein. Python-/Tool-Downloads und Caches liegen unter der ignorierten `.local/`, die Python-Umgebung unter `.venv/`. Es werden keine fremden StudyCenter-Daten oder Browserprofile verwendet, keine globalen Python-Pakete installiert und keine System-Tesseract-Installer gestartet.

Der Windows-Starter benötigt die bereits vorhandenen Windows-Werkzeuge `curl.exe` und `tar.exe`. Der Linux-Starter benötigt `curl`, `tar`, `sha256sum` und für die portable Komponentenextraktion die vorhandenen Ubuntu-Werkzeuge `apt-get`, `apt-cache` und `dpkg-deb`. Er installiert **keine Host-Pakete**. Wenn diese Voraussetzungen fehlen, nutze das fertige Release oder kläre sie separat.

## Quellcode aktualisieren

Anwendung vollständig beenden, dann im Projektordner `git pull` ausführen und denselben Starter erneut starten. Der Starter prüft, ob die Entwicklungsumgebung nach einer Änderung erneut vorbereitet werden muss. **Ein Git-Update ist kein Update des bereits installierten Windows-Releasepakets.** Beide Varianten getrennt behandeln.

## Nützliche Befehle

Windows im geklonten Projekt:

```powershell
.\start.cmd --setup-only
.\start.cmd --no-browser --port 0
.\start.cmd --stop
.\.venv\Scripts\python.exe -m pytest
```

Ubuntu im geklonten Projekt:

```sh
sh start.sh --setup-only
sh start.sh --no-browser --port 0
sh start.sh --stop
.venv/bin/python -m pytest
```

`--setup-only` richtet Komponenten ein, ohne die Anwendung zu öffnen; `--no-browser` startet ohne grafische Browseröffnung, `--port 0` wählt einen freien lokalen Port, `--stop` beendet die eigene gestartete Instanz. Die lokalen Daten, Browserprofile und Credentials gehören zur Public-Instanz unter `.local/data`. Mit `--data-dir <neuer-ordner>` kann ein anderer **eigener** Datenbestand verwendet werden; denselben Pfad für `--stop` angeben. **Nie auf Datenverzeichnisse anderer Apps zeigen.**

Falls ein Download fehlschlägt, Verbindung prüfen und denselben Starter erneut ausführen. Abgebrochene Komponenten werden nicht stillschweigend als vollständig akzeptiert. Bei einem nicht kompatiblen geklonten `.venv` sichert der Starter die vorherige Umgebung im eigenen `.local/` und erstellt eine neue.

## Tests, Beiträge und technische Referenz

- Tests nutzen synthetische Inhalte und isolierte Verzeichnisse, keine echten TU- oder KI-Konten. Erfolgreiche Tests ersetzen keine reale Konto- oder Desktopabnahme.
- Vor einem öffentlichen Commit ausgewählte Dateien prüfen, `scripts/check_public.py --staged` ausführen und keine Credentials, privaten Daten oder lokalen Reports hochladen.
- Technische Schnittstellen: [CONTRACTS.md](CONTRACTS.md), [PROVIDERS.md](PROVIDERS.md), [OPTIONAL.md](OPTIONAL.md). Build/Installer: [PACKAGING.md](PACKAGING.md). Nachweise und bekannte Einschränkungen: [RELEASE-VERIFICATION.md](RELEASE-VERIFICATION.md).
- Das private persönliche StudyCenter und dieses Public-Projekt sind vollständig getrennt.

[**← Zur Startseite**](../README.md) · [Schnellstart ohne Git](QUICKSTART.md)
