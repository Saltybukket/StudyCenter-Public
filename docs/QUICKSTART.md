# StudyCenter Public einrichten

StudyCenter speichert deinen persönlichen Bestand auf diesem Rechner. Beim ersten
Start ist er leer. Notizen, eigene Termine und Dateiimport funktionieren ohne Konto.

## Windows

Voraussetzung ist eine vorhandene **Microsoft Visual C++ v14 Runtime (x64)**.
Falls sie fehlt, verweist der Setup auf den
[offiziellen Microsoft-Download](https://learn.microsoft.com/en-us/cpp/windows/latest-supported-vc-redist).
Die Microsoft-Runtime gehört in diesem Beta-Build zu den Systemvoraussetzungen.
Visual Studio wird zum Benutzen der Anwendung nicht benötigt.

Öffne das Windows-Setup aus dem geprüften Release. Übernimm den empfohlenen
Programm- und Datenordner oder wähle einen eigenen beschreibbaren Ort. Starte
anschließend **StudyCenter-Public** im Startmenü. Ein unsignierter Pilot kann eine
Windows-Sicherheitsnachfrage auslösen. Die Schutzfunktionen bleiben eingeschaltet.

## Linux

Entpacke das Linux-Paket in einen eigenen Ordner und öffne **start.sh**. Die
Oberfläche erscheint im Standardbrowser. Persönliche Daten liegen standardmäßig
im XDG-Datenordner. Ein eigener Datenort kann dem Starter mit
`--data-dir "/gewählter/Ordner"` übergeben werden. **start.sh --stop** beendet
die dazugehörige Instanz; bei eigenem Datenort dieselbe Ortsangabe verwenden.

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
