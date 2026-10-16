# StudyCenter Public

StudyCenter bündelt Aufgaben, Notizen, Termine und Originalquellen in einer lokalen Anwendung. Für Studierende der TU Graz entwickelt, funktioniert es auch ohne TU-Konto und ohne KI.

**Windows 10/11 x64:** [beta.3-Setup herunterladen](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.3/studycenter-public-0.1.0b3-windows-x86_64-setup.exe), öffnen und einrichten.

**Linux x64:** [beta.3-Slim-Archiv herunterladen](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.3/studycenter-public-0.1.0b3-linux-x86_64-slim.tar.gz), entpacken und `sh start.sh` ausführen.

[SHA-256-Prüfsummen](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.3/SHA256SUMS) · [Release mit Größenangaben](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.3)

Bei einem Update den bisherigen Public-Datenordner beibehalten. Die [beta.2-Komplettpakete](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.2) bleiben unverändert verfügbar; ein bereits eingerichteter lokaler Datenbestand wird weiterverwendet.

## Benutzen

1. Windows-Setup öffnen oder das Linux-Archiv entpacken und starten.
2. Programm- und eigenen Datenordner bestätigen. Eine fehlende Microsoft-Laufzeit wird über den offiziellen Download mit sichtbarer Bestätigung eingerichtet.
3. Den ersten Download abschließen; danach dieselbe Verknüpfung beziehungsweise denselben Starter verwenden.
4. Eine Aufgabe oder Notiz anlegen oder ein PDF importieren. PDF-Anzeige und Text-PDF-Verarbeitung gehören zur Basis.
5. Bei Bedarf TU-Quellenbrowser, OCR oder Sprache ausdrücklich einrichten. Sprachmodellgewichte werden nach Größenanzeige separat bestätigt.

Die Slim-Basis nutzt eine eigene Python-Umgebung: geeignetes CPython 3.12 x64 wird wiederverwendet, andernfalls eine eigene Laufzeit geladen. Git und vorinstalliertes Python sind für den Release-Download nicht nötig. Der erste Aufbau braucht Internet; vorbereitete lokale Funktionen laufen anschließend offline.

## Aus Git starten

Repository klonen und im Checkout `start.cmd` unter Windows beziehungsweise `sh start.sh` unter Linux ausführen. [Befehle und Voraussetzungen](docs/DEVELOPMENT.md).

## Entwickeln

[Entwicklung](docs/DEVELOPMENT.md) · [Quickstart](docs/QUICKSTART.md) · [Optionale Funktionen](docs/OPTIONAL.md) · [Drittanbieter-Lizenzen](docs/THIRD_PARTY.md)

Plane Fristen im Kalender, vergleiche Originalbelege und prüfe KI-Vorschläge vor dem Übernehmen. Daten bleiben im eigenen lokalen Bestand. TU-Abgleich erfolgt auf deinen Klick; Cloud-KI ist freiwillig und kann Kosten verursachen. Es gibt keinen automatischen Modell- oder Anbieterwechsel.

Unter Linux öffnet sich die Oberfläche im Systembrowser. Der getrennte TU-Quellenbrowser kann durch AppArmor eingeschränkt sein; lokale Funktionen bleiben nutzbar. [Prüfstand und Grenzen](docs/RELEASE-VERIFICATION.md).

Lizenz: [AGPL-3.0-only](LICENSE).
