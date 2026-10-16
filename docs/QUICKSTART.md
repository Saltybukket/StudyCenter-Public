# StudyCenter Public einrichten

StudyCenter speichert deinen persönlichen Bestand auf diesem Rechner. Beim ersten
Start ist er leer. Notizen, eigene Termine und Dateiimport funktionieren ohne Konto.

## Windows

Lade das beta.2-Windows-Setup herunter und starte es. Übernimm den empfohlenen
Programm- und Datenordner oder wähle einen eigenen beschreibbaren Ort. Das Setup
erkennt eine fehlende **Microsoft Visual C++ v14 Runtime (x64)** und lädt den
offiziellen Microsoft-Installer mit geprüfter SHA256 herunter. Bestätige dessen
Lizenzbedingungen und Windows-Administratorabfrage; anschließend setzt das
StudyCenter-Setup die Installation fort. Eine kompatible vorhandene Runtime
wird nicht verändert. Falls Microsoft einen Neustart verlangt, starte Windows
selbst neu und öffne das Setup noch einmal. Visual Studio ist nicht erforderlich.

Am Ende des Setups **StudyCenter-Public starten** gewählt lassen; später genügt
der Eintrag im Startmenü. Ein unsignierter Beta-Installer kann eine
Windows-Sicherheitsnachfrage auslösen. Die Schutzfunktionen bleiben eingeschaltet.

## Linux

Entpacke das Linux-Paket in einen eigenen Ordner und führe dort `sh start.sh` aus. Die
Oberfläche erscheint im Standardbrowser. Persönliche Daten liegen standardmäßig
im XDG-Datenordner. Ein eigener Datenort kann dem Starter mit
`--data-dir "/gewählter/Ordner"` übergeben werden. **start.sh --stop** beendet
die dazugehörige Instanz; bei eigenem Datenort dieselbe Ortsangabe verwenden.
Python, OCR und die Anwendungsabhängigkeiten sind bereits enthalten.

Die Oberfläche läuft im normalen Systembrowser. Der Browser für **TU verbinden**
und den manuellen Quellenabruf ist davon getrennt und verwendet ein eigenes
Public-Profil. Falls Ubuntu den mitgelieferten Browser blockiert, verwendet die
Anwendung bei passender Restriktion vorhandenes, rootverwaltetes System-Chrome.
Vorhandene Browserinstallationen und ihre Profile werden nicht verändert.
Ist dieser Quellenbrowser nicht verfügbar, funktionieren eigene Einträge,
Dateiimport und Dokumentleser weiter. Sicherheitsregeln bleiben eingeschaltet.

## Repository verwenden

Nach `git clone https://github.com/Saltybukket/StudyCenter-Public.git` im neuen
Ordner `start.cmd` (Windows) oder `sh start.sh` (Ubuntu 24.04 x64) ausführen.
Der erste Start lädt die nötigen Werkzeuge und Komponenten in diesen Ordner;
spätere Starts verwenden sie wieder. Weitere Befehle sind unter
[Entwicklung](DEVELOPMENT.md) beschrieben.

## Die ersten fünf Schritte

1. Unter **Einrichtung → Speicher** den angezeigten Datenort prüfen.
2. Unter **Kurse & Quellen** eine eigene TU-Quelle speichern. **TU verbinden**
   öffnet das echte Anmeldefenster. Melde dich selbst an; Passwörter und MFA
   gehören dort hinein. Mit **Eigene Kurse erkennen** anschließend Kurse wählen.
3. Unter **KI-Verbindung** deinen Anbieter und ein Modell wählen. Eigenen
   API-Zugang oder einen vorhandenen lokalen Modellserver verwenden; dieser
   Schritt ist freiwillig. ChatGPT verbindet dein eigenes Konto im Systembrowser.
4. Bei der Quelle bewusst **Jetzt synchronisieren** drücken. Abruf, Leselücken
   und Vollständigkeit werden angezeigt. Beim Start läuft kein Quellenabgleich.
5. Unter **Aufgaben & Notizen** eigene Einträge erstellen oder unter
   **Dokumente** eine eigene Datei importieren. Jede KI-Auswertung startet auf
   deinen Klick; Vorschau prüfen und **Übernehmen** wählen.

## Wenn etwas fehlt

Ein ungültiger TU-Zugang benötigt eine neue eigene Anmeldung. Bei fehlender
KI-Verbindung Anbieter, Basisadresse und Modell unter **KI-Verbindung** prüfen.
Der **synthetische Verbindungstest** enthält keine Studieninhalte, kann beim
Anbieter aber kostenpflichtig sein. Es gibt keinen automatischen Providerwechsel.
Bei Leselücken das Original im Dokumentleser vergleichen und bei Bedarf
**Seite neu lesen** wählen. Fehlerhafte Datumsangaben über **Quellenvergleich**
als persönliche Entscheidung behandeln; eine Planung ist keine offizielle Buchung.

Mail, Sprache und lokales MCP sind freiwillig. Sprachmodelle werden erst nach
deiner ausdrücklichen Downloadwahl geladen. **Einstellungen → Glocke & Verlauf**
zeigt Meldungen unabhängig vom Aufgabenstatus. Ein ausgeschalteter Rechner meldet nichts.

**Sicherung erstellen** enthält private Studieninhalte und Originale, aber keine
Zugänge oder Browserprofile. Teile diese Datei nicht öffentlich. Eine
Wiederherstellung zeigt zuerst die Vorschau und erstellt eine Rückfallsicherung.

Windows-Hintergrundbetrieb lässt sich unter Einstellungen wählen. **Vollständig
beenden** im Tray oder in der Oberfläche stoppt ausschließlich diese Public-Instanz.

Ausgewählte Inhalte gehen nur bei einer ausdrücklich ausgelösten KI-Aktion an
deinen gewählten Anbieter. Cloud-KI rechnet außerhalb deines Rechners und kann
Kosten verursachen. Es gibt keine automatische Telemetrie.
