# StudyCenter Public – Installation und erste Schritte

**Du möchtest StudyCenter einfach benutzen? Dann lade das fertige Paket herunter. Git und Python sind dafür nicht nötig.**

## 1. Download

- **Windows 10/11 x64:** [**Installer (.exe) herunterladen**](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-windows-x86_64-setup.exe)
- **Linux x64:** [**Fertiges Paket (.tar.gz) herunterladen**](https://github.com/Saltybukket/StudyCenter-Public/releases/download/v0.1.0-beta.2/studycenter-public-0.1.0b2-linux-x86_64.tar.gz)
- [Alle Dateien und SHA-256-Prüfsummen der aktuellen beta.2](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.2)

**Auf GitHub nicht gefunden?** Öffne die [Release-Seite](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.2), klappe **Assets** auf und klicke auf die Datei mit `windows_x86_64-setup.exe`. **Code → Download ZIP** ist nur der Quellcode, kein Windows-Installer.

## 2. Windows installieren

1. Die heruntergeladene `studycenter-public-0.1.0b2-windows-x86_64-setup.exe` öffnen.
2. Programmordner und **eigenen Datenordner** auswählen; die Vorschläge sind für den Anfang geeignet.
3. Falls die **Microsoft Visual C++ v14 Runtime (x64)** fehlt, bietet StudyCenter den offiziellen Microsoft-Download an. Den sichtbaren Microsoft-Lizenz- und gegebenenfalls Administrator-Dialog selbst bestätigen. Eine passende vorhandene Runtime wird nicht erneut installiert.
4. Falls Microsoft einen Neustart verlangt, Windows normal neu starten und das StudyCenter-Setup erneut öffnen.
5. Am Ende **StudyCenter-Public starten** auswählen oder später über das Windows-Startmenü öffnen.

Python, Tesseract, Browser-Automation und die übrige Anwendungslaufzeit sind im Paket enthalten. Visual Studio ist **nicht** erforderlich. Eine unsignierte Beta kann einen Windows-Sicherheitsdialog auslösen: Prüfe vor der Ausführung die Herkunft der Datei und gegebenenfalls die SHA-256-Prüfsumme. Keine Windows-Schutzfunktionen abschalten.

## 3. Linux starten

1. Das Linux-`.tar.gz` herunterladen und in einen eigenen Ordner entpacken.
2. Ein Terminal im entpackten Anwendungsordner öffnen und ausführen:

```sh
sh start.sh
```

3. Die Oberfläche erscheint im normalen Systembrowser. Zum Beenden im selben Ordner `sh start.sh --stop` verwenden.

Ein anderer Datenordner kann über `sh start.sh --data-dir "/dein/Ordner"` gewählt werden; beim Stoppen denselben `--data-dir`-Wert verwenden. Das Paket enthält Python, Abhängigkeiten und OCR bereits. Zielplattform ist Ubuntu 24.04 x64 beziehungsweise ein kompatibles Linux-System.

**Linux-Einschränkung:** Die normale Oberfläche ist von der Playwright-basierten TU-Anmeldung getrennt. Unter bestimmten Ubuntu-AppArmor-/Browser-Berechtigungen kann der TU-Quellenbrowser nicht starten. Eigene Aufgaben, Dateiimport, PDF-Leser und lokale Funktionen bleiben verwendbar. Es werden keine Sandbox-Sicherheitsregeln abgeschaltet.

## 4. Das erste Mal verwenden

1. **Einrichtung → Speicher:** Speicherort prüfen. Ein neuer Nutzer beginnt ohne fremde Kurse oder Daten.
2. **Aufgaben & Notizen** oder **Dokumente:** direkt eine eigene Aufgabe, Notiz oder PDF anlegen – funktioniert offline und ohne Konto.
3. **Kurse & Quellen (optional):** eigene TU-Quelle hinzufügen, **TU verbinden** öffnen und dich im echten Anmeldefenster anmelden. Danach **Eigene Kurse erkennen** und relevante Kurse auswählen.
4. **Jetzt synchronisieren (optional):** Quellen nur auf deinen Klick einlesen. Prüfe Lücken, Originalbelege und mögliche Terminwidersprüche im **Quellenvergleich**.
5. **KI-Verbindung (optional):** einen Anbieter oder lokalen Modellserver auswählen. Bei Cloud-Anbietern dein eigenes Konto oder deinen API-Key verwenden. Ergebnisse erst prüfen und dann **Übernehmen** wählen.

Eine persönliche Planung ersetzt keine offizielle Prüfungs- oder Lehrveranstaltungsanmeldung. Cloud-KI-Aktionen können kostenpflichtig sein; es gibt keinen automatischen Anbieterwechsel oder ungefragten Modellaufruf.

## 5. Wenn etwas nicht funktioniert

| Problem | Was tun? |
| --- | --- |
| **Windows-Setup meldet fehlende Runtime** | Den offiziellen Microsoft-Dialog abschließen. Wenn der Download fehlschlägt oder die Freigabe abgelehnt wurde, erneut bei bestehender Internetverbindung versuchen. |
| **Windows startet nicht** | Über das Startmenü öffnen; prüfen, ob die Setup- und Laufzeitmeldungen vollständig abgeschlossen wurden. |
| **TU-Quelle zeigt keinen Zugriff** | **TU verbinden** erneut öffnen und die Anmeldung/MFA selbst abschließen. Quellen werden nicht automatisch aktualisiert. |
| **PDF-Text fehlt** | Original im Dokumentleser ansehen und bei Bedarf **Seite neu lesen** verwenden; nicht lesbare Bereiche können bestehen bleiben. |
| **KI funktioniert nicht** | Unter **KI-Verbindung** Anbieter, Modell und Zugang kontrollieren. Ein Verbindungstest kann beim Anbieter Kosten auslösen. |
| **Linux: TU-Browser blockiert** | Lokale Funktionen weiterverwenden; siehe [aktuelle bekannte Grenzen](RELEASE-VERIFICATION.md). Keine AppArmor-Ausnahmen oder `--no-sandbox` aktivieren. |

**Daten und Updates:** Updates erhalten den eigenen Datenbestand; eine Deinstallation löscht persönliche Daten standardmäßig nicht. **Sicherung erstellen** kann Originale und private Studieninformationen enthalten, aber keine Zugangsdaten oder Browserprofile. Backups nicht öffentlich teilen. Die Wiederherstellung zeigt zuerst eine Vorschau. **Vollständig beenden** stoppt die eigene Instanz; Erinnerungen werden nicht ausgelöst, wenn die Anwendung beziehungsweise der Rechner nicht läuft.

## 6. Lieber direkt aus Git starten?

Für Entwickler (nicht nötig für die normale Nutzung):

```sh
git clone https://github.com/Saltybukket/StudyCenter-Public.git
cd StudyCenter-Public
```

Danach unter Windows `start.cmd` beziehungsweise unter Ubuntu 24.04 x64 `sh start.sh` ausführen. Der erste Start richtet die benötigten Werkzeuge im Checkout ein und benötigt Internet. Ausführliche Hinweise: [Entwicklung](DEVELOPMENT.md).

[**← Zur Repository-Startseite**](../README.md)
