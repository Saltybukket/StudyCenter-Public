# StudyCenter Public

StudyCenter bündelt Aufgaben, Notizen, Termine und Originalquellen in einer lokalen Anwendung. Für Studierende der TU Graz entwickelt, funktioniert es auch ohne TU-Konto und ohne KI.

**Windows 10/11 x64:** [Veröffentlichten beta.2-Installer herunterladen](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-windows-x86_64-setup.exe).

**Linux x64:** [Veröffentlichtes beta.2-Komplettpaket herunterladen](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-linux-x86_64.tar.gz), entpacken und `sh start.sh` ausführen.

[Release-Dateien und SHA-256-Prüfsummen](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.2)

> **beta.3 Slim wird vorbereitet.** Dafür gibt es hier noch keinen veröffentlichten Download. Das bestehende beta.2-Komplettpaket bleibt verfügbar; es enthält Python, Browser und OCR bereits. Die folgenden Hinweise zur bedarfsgesteuerten Einrichtung gelten für beta.3 und den entsprechend aktualisierten Quellcode.

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
