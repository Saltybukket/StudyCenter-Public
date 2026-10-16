# StudyCenter Public

StudyCenter Public verbindet Aufgaben, persönliche Notizen, Termine und ihre
Originalquellen in einer lokalen Anwendung für TU-Graz-Studierende. Jede neue
Installation beginnt leer. Eigene Einträge und Dateiimport funktionieren ohne
TU-Konto oder KI-Verbindung. Übersicht, Post-its, Pins, Timeline, Kalender,
Gantt, Archiv und Dokumentleser zeigen denselben persönlichen Bestand.

**beta.2:** Wähle auf der
[Release-Seite](https://github.com/Saltybukket/StudyCenter-Public/releases)
das passende Paket:

- **Windows:** `studycenter-public-0.1.0b2-windows-x86_64-setup.exe`
  herunterladen und starten. Die empfohlenen Ordner übernehmen; danach öffnet
  sich die Anwendung. Fehlende Microsoft-Laufzeit erkennt das Setup und lädt
  deren offiziellen Installer. Microsoft-Lizenz und Windows-Administratorabfrage
  gegebenenfalls bestätigen. Visual Studio ist nicht erforderlich.
- **Linux:** `studycenter-public-0.1.0b2-linux-x86_64.tar.gz` entpacken und im
  entpackten Ordner `sh start.sh` ausführen.
- **Entwicklung:** Repository klonen und `start.cmd` (Windows) beziehungsweise
  `sh start.sh` (Ubuntu 24.04 x64) ausführen. Der erste Start richtet Python,
  eigene virtuelle Umgebung, Abhängigkeiten, Browser und OCR lokal ein.

Prüfsummen liegen beim Release. Internet wird für den ersten Entwicklerstart
und gegebenenfalls den Microsoft-Laufzeitdownload benötigt.

Das Windows-Paket richtet sich an Windows 10/11 x64 und enthält seine eigene
Python-, Browser- und OCR-Laufzeit. Es bietet einen eigenen Datenordner,
Startmenü und Tray; Autostart ist freiwillig. Linux verwendet dieselbe Oberfläche
im Standardbrowser und wird auf Ubuntu 24.04 x64 geprüft. Den tatsächlichen
Plattformteststand und offene Abschlussprüfungen beschreibt [PLAN](docs/PLAN.md).

Unter Linux sind Oberfläche im Systembrowser und automatisierter Quellenbrowser
getrennte Funktionen. Ubuntu kann den mitgelieferten Quellenbrowser durch seine
AppArmor-Regeln blockieren. Bei dieser Restriktion kann StudyCenter vorhandenes,
rootverwaltetes Chrome unter seinem bestehenden Systemprofil verwenden, mit
einem eigenen Public-Browserprofil. Fehlt diese Möglichkeit, bleiben eigene
Einträge, Dateiimport und Dokumentleser verfügbar. StudyCenter installiert keine
AppArmor-Ausnahmen und startet Chromium nicht ohne Sandbox.

Das Windows-Setup prüft die **Microsoft Visual C++ v14 Runtime (x64)**. Eine
kompatible vorhandene Runtime bleibt unverändert. Fehlende Komponenten lädt es
direkt von Microsoft; Lizenzannahme und UAC bleiben sichtbar. Die Anwendung
enthält keine fremden Runtime-DLLs und keinen Tesseract-Systeminstaller.

Nach der Installation:

1. Unter **Einrichtung → Speicher** den eigenen Datenort prüfen.
2. Unter **Kurse & Quellen** eine Quelle speichern, **TU verbinden** öffnen,
   selbst anmelden und die gewünschten Kurse auswählen.
3. Unter **KI-Verbindung** einen eigenen Anbieter oder lokalen Modellserver
   einrichten; dieser Schritt kann übersprungen werden.
4. Bewusst **Jetzt synchronisieren** wählen und angezeigte Leselücken prüfen.
5. Eigene Aufgaben anlegen oder Dokumente importieren; KI-Vorschauen vor dem
   Übernehmen lesen. Widersprüchliche Termine im **Quellenvergleich** prüfen.

Persönliche Planung ersetzt keine offizielle Anmeldung. Quellenabgleich startet
ausschließlich auf deinen Klick. Mail, Sprache und lokale Agentenverbindungen
sind freiwillig. Sprachmodelle werden erst nach einer ausdrücklichen Downloadwahl
geladen. Backups enthalten private Studieninhalte, aber keine Zugangsdaten oder
Browserprofile, und sollten nicht öffentlich geteilt werden.

Ausgewählte Inhalte gehen nur bei einer bewusst ausgelösten KI-Aktion an deinen
gewählten Anbieter. Cloud-KI rechnet außerhalb deines Rechners und kann Kosten
verursachen. Es gibt keine automatische Telemetrie. **Vollständig beenden**
stoppt die eigene Instanz; Deinstallation erhält persönliche Daten standardmäßig.

[Quickstart](docs/QUICKSTART.md) · [Entwicklung](docs/DEVELOPMENT.md) ·
[Drittanbieter](docs/THIRD_PARTY.md). Quellcode und eigene Assets stehen unter
**AGPL-3.0-only**; der vollständige Text liegt in [LICENSE](LICENSE).
