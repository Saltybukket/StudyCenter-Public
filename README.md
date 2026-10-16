# StudyCenter Public

**Dein Studium im Blick:** Aufgaben, Notizen, Termine und Originalquellen in einer lokalen Anwendung. Entwickelt für Studierende der TU Graz, auch ohne TU-Konto oder KI nutzbar.

## ⬇️ Jetzt herunterladen – beta.2

**🪟 Windows 10/11 (x64): [StudyCenter-Installer herunterladen (.exe)](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-windows-x86_64-setup.exe)**

**🐧 Linux (x64): [Fertiges Linux-Paket herunterladen (.tar.gz)](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-linux-x86_64.tar.gz)**

[Alle Downloads, Versionshinweise und SHA-256-Prüfsummen](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.2)

> **Wo liegt der Installer?** Auf GitHub rechts unter **Releases → v0.1.0-beta.2 → Assets**. Dort die Datei mit `windows_x86_64-setup.exe` wählen. Der grüne Button **Code → Download ZIP** lädt nur den Quellcode, **nicht** den fertigen Installer.

### In wenigen Schritten starten

- **Windows:** Die heruntergeladene `.exe` öffnen, Installations- und Datenordner wählen, StudyCenter starten. Wenn Microsoft Visual C++ fehlt, bietet das Setup den offiziellen Microsoft-Installer an; Lizenz-/Windows-Bestätigung bleibt sichtbar. **Python, Git und Tesseract musst du nicht installieren.**
- **Linux:** Archiv entpacken, Terminal im entpackten Ordner öffnen und `sh start.sh` ausführen. StudyCenter öffnet sich im Standardbrowser. Python und portable OCR sind enthalten.
- **Quellcode/Entwicklung:** Repository klonen und im Ordner `start.cmd` (Windows) oder `sh start.sh` (Ubuntu 24.04 x64) ausführen. Der erste Start richtet die Entwicklungsumgebung im Projektordner selbst ein. [Details für Entwickler](docs/DEVELOPMENT.md).

[**Schritt-für-Schritt-Anleitung für Windows, Linux und den ersten Start →**](docs/QUICKSTART.md)

## Was kann StudyCenter?

- **Planen:** Aufgaben, Notizen, Post-its, Pins, Kalender mit Wochenansicht, Timeline und Gantt.
- **Nachvollziehen:** Kursquellen, PDF-Dokumente, OCR, Originalbelege und Quellenvergleich.
- **Entscheiden:** KI-Vorschläge prüfen, übernehmen oder konfliktgeschützt rückgängig machen.
- **Erinnern:** Fristen, eigene Termine, Benachrichtigungsglocke und Verlauf.
- **Optional verbinden:** TU-Quellen manuell synchronisieren, ChatGPT oder andere KI-Anbieter, lokale Modelle, Sprache und lokales MCP.

Neue Installationen beginnen **mit einem leeren, eigenen Datenbestand**. Für Aufgaben, Notizen und Dateiimport ist keine Anmeldung nötig. Die Verbindung zu TU-Quellen oder KI ist freiwillig.

## Dein erster Start

1. **Einrichtung → Speicher:** eigenen Datenordner prüfen.
2. **Aufgaben & Notizen** oder **Dokumente:** eine Aufgabe anlegen oder ein PDF importieren – ganz ohne Anmeldung.
3. Optional unter **Kurse & Quellen** selbst anmelden, Kurse auswählen und bewusst **Jetzt synchronisieren** klicken.
4. Optional unter **KI-Verbindung** einen Anbieter oder lokalen Modellserver einrichten; Vorschläge vor **Übernehmen** prüfen.

StudyCenter führt **keinen automatischen TU-Quellenabgleich** aus. Eigene Planungen ersetzen keine offiziellen Anmeldungen. Daten liegen lokal; nur bei einer ausdrücklich ausgelösten Cloud-KI-Aktion gehen die dafür ausgewählten Inhalte an den gewählten Anbieter. Cloud-KI kann Kosten verursachen. Es gibt keine automatische Telemetrie. Sicherungen können sensible Studieninhalte enthalten und gehören nicht in ein öffentliches Repository.

## Beta-Hinweise

- **Windows:** Das Setup kann bei fehlenden Microsoft-Laufzeitkomponenten deren offiziellen Installer anbieten. Die Laufzeit- und gegebenenfalls Sicherheitsdialoge müssen bestätigt werden; der Beta-Installer ist nicht signiert.
- **Linux:** Die lokale Oberfläche funktioniert im normalen Browser. Der automatisierte TU-Quellenbrowser kann auf Ubuntu durch AppArmor beziehungsweise die Browser-Eigentumsprüfung blockiert sein. Die lokalen Funktionen bleiben nutzbar.
- **Noch nicht vollständig im Alltag abgenommen:** persönliche TU-/KI-Konten, Mikrofon/TTS sowie native Fenster-/Tray-Bedienung auf einem gewöhnlichen Windows-11-Rechner.

Aktueller Prüfstand und bekannte Grenzen: [Release-Verifikation](docs/RELEASE-VERIFICATION.md).

## Dokumentation und Mitmachen

[**Quickstart**](docs/QUICKSTART.md) · [**Entwicklung und Git-Installation**](docs/DEVELOPMENT.md) · [KI-Anbieter](docs/PROVIDERS.md) · [Optionale Funktionen](docs/OPTIONAL.md) · [Drittanbieter-Lizenzen](docs/THIRD_PARTY.md)

StudyCenter Public steht unter **AGPL-3.0-only** ([LICENSE](LICENSE)). Für mitgelieferte Fremdkomponenten gelten zusätzlich deren jeweilige Lizenzbedingungen.
