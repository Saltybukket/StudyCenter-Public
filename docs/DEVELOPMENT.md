# Aus Git starten und entwickeln

```sh
git clone https://github.com/Saltybukket/StudyCenter-Public.git
cd StudyCenter-Public
```

Windows (PowerShell):

```powershell
.\start.cmd
```

Linux (Ubuntu 24.04 x64):

```sh
sh start.sh
```

Git ist nur zum Klonen nötig. Der erste Aufbau benötigt Internet und freien Speicher. Die gemeinsame Setup-Logik verwendet zuerst eine funktionierende eigene Umgebung, dann geeignetes vorhandenes **CPython 3.12 x64**, andernfalls eine eigene verwaltete Laufzeit. Andere Python-Versionen oder Architekturen sind für diesen Setupweg nicht freigegeben.

`uv` richtet eine eigene Umgebung unter `.local/environments/` ein. `.local/python-path.txt` nennt deren Interpreter; eine gültige bestehende eigene `.venv/` kann weiterverwendet werden. Werkzeuge, verwaltetes Python und Downloadcache liegen ebenfalls unter `.local/`. Ein vorhandenes System-Python dient ausschließlich als Interpreter: kein globales pip, keine fremden Pakete oder Profile.

Die Basis startet ohne Chromium, OCR und Sprache. Gewünschte Komponenten werden über die Anwendung ausdrücklich eingerichtet. Sprachmodellgewichte brauchen eine eigene Downloadbestätigung. Das veröffentlichte beta.2-Komplettpaket enthält seine Komponenten bereits; die Slim-Änderungen gelten für beta.3 beziehungsweise den aktualisierten Checkout.

Für den Bootstrap braucht Windows die vorhandenen Werkzeuge `curl.exe` und `tar.exe`, Linux `curl`, `tar` und `sha256sum`. Es werden keine Host-Pakete installiert. Linux verwendet für die Oberfläche den Systembrowser; Einschränkungen des TU-Quellenbrowsers durch AppArmor sind davon getrennt.

Den Checkout über seinen physischen Ordner öffnen. Umgeleitete Pfade (Symlinks oder Windows-Junctions) werden zum Schutz fremder Installationen abgewiesen; dieselben Dateien im tatsächlichen Zielordner starten.

## Aktualisieren und stoppen

Anwendung vollständig beenden, im Checkout `git pull` ausführen und denselben Starter verwenden. Ein Git-Update aktualisiert diesen Checkout; es ersetzt kein installiertes Releasepaket. Normale Starts verwenden die vorbereitete Umgebung. Der erste Aufbau und bewusst angeforderte Downloads benötigen Internet.

Windows:

```powershell
.\start.cmd --stop
.\start.cmd --help
.\start.cmd --setup-only
.\start.cmd --no-browser --port 0
```

Linux:

```sh
sh start.sh --stop
sh start.sh --help
sh start.sh --setup-only
sh start.sh --no-browser --port 0
```

`--stop` beendet die eigene Instanz. `--setup-only` bereitet die Basis ohne App-Start vor. `--no-browser` unterdrückt die Browseröffnung; `--port 0` wählt einen freien lokalen Port. Hilfe und Stop sollen keine Komponenten nachladen.

Standardmäßig gehören Daten zum eigenen Public-Bestand unter `.local/data`. Mit `--data-dir <eigener-ordner>` einen anderen eigenen Speicher wählen und beim Stoppen dieselbe Angabe verwenden. Bei einem Übergang von beta.2 den bisherigen Datenort bewusst beibehalten. Keine Datenordner anderer Anwendungen verwenden.

Bei abgebrochenem Download die Einrichtung wiederholen. Ist ein zuvor genutztes System-Python entfernt worden, die angebotene Reparatur mit eigener Laufzeit nutzen. Keine fremden Caches oder Umgebungen bereinigen.

## Testen

Entwicklungswerkzeuge sind kein Bestandteil der normalen Nutzerinstallation. Nach `start.cmd --setup-only` unter PowerShell:

```powershell
$python = Get-Content .local/python-path.txt
& .local/tools/uv-0.8.22/uv.exe --no-config pip install --python $python --constraint requirements-windows.lock pytest==9.1.1 pytest-asyncio==1.4.0
& $python -m pytest
```

Unter Linux:

```sh
python=$(cat .local/python-path.txt)
.local/tools/uv-0.8.22/uv --no-config pip install --python "$python" --constraint requirements-linux.lock pytest==9.1.1 pytest-asyncio==1.4.0
"$python" -m pytest
```

Tests verwenden synthetische Inhalte und isolierte Datenverzeichnisse. Ausgeführte Protokoll-, Paket-, Browser- und Betriebssystemprüfungen getrennt berichten; simulierte Anbieter ersetzen keine persönliche Kontoabnahme.

## Verzeichnisse und Beiträge

- `src/`: Anwendung und eingebettete Ressourcen.
- `tests/`: isolierte Regressionstests.
- `scripts/`: Setup, Build und vorhandene Prüfwerkzeuge.
- `packaging/`: Plattformstarter und Installer.
- `docs/`: kurze Anleitungen und technische Referenz.

Vor jedem Commit nur benannte Dateien stagen, den Inhalt prüfen und `scripts/check_public.py --staged` ausführen. Vor dem Push ausgehende Commits prüfen. Keine Daten, Profile, Binärpakete, lokalen Reports oder Zugangsdaten committen.

[Schnittstellen](CONTRACTS.md) · [Provider](PROVIDERS.md) · [Optionale Funktionen](OPTIONAL.md) · [Build und Installer](PACKAGING.md) · [Drittanbieter](THIRD_PARTY.md) · [Prüfstand](RELEASE-VERIFICATION.md) · [Quickstart](QUICKSTART.md)
