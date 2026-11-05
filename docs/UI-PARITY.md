# Vergleich mit der privaten Referenz

Der Vergleich betrachtet Code, Templates, statische Ressourcen und Tests der
aktuellen privaten Referenz einschließlich nicht eingecheckter Änderungen.
Er ist eine Arbeitskarte, keine Behauptung vollständiger Gleichheit. Es wurden
keine Konten, Profile oder persönlichen Daten für den Vergleich verwendet.
Der lokale, nicht versionierte Arbeitsbericht enthält Hashes der verglichenen
Quell- und Testdateien; sein Zeitpunkt ist entscheidend, weil die Referenz
weiter verändert werden kann.

Für eine neue Momentaufnahme und spätere Driftprüfung die beiden ausdrücklich
gewählten Quell- und Testverzeichnisse als `CODE_DIR` und `TESTS_DIR` angeben:

```powershell
python scripts/reference_manifest.py capture --code-dir CODE_DIR --tests-dir TESTS_DIR --manifest .local/reference.json
python scripts/reference_manifest.py verify --code-dir CODE_DIR --tests-dir TESTS_DIR --manifest .local/reference.json
```

Das Werkzeug liest nur ausgewählte Dateitypen aus diesen beiden Bäumen, schließt
Daten- und Profilverzeichnisse sowie Links aus und überschreibt kein Manifest.
Es vergleicht Änderungen, neue und entfernte Dateien. Der Bericht bleibt lokal.

| Bereich | Public-Stand und verbleibender Unterschied |
| --- | --- |
| Navigation und Einstellungen | Die frühere Übernahme hat Navigation, Karten und sechs Einstellungsregister angeglichen. Nicht alle zwischenzeitlichen privaten UI- und Interaktionsänderungen sind übertragen. Windows- und Linux-spezifische Betriebsoptionen bleiben erhalten. |
| Übersicht und Aufgaben | Fenster mit unbekanntem Schluss werden nicht mit dem Öffnungsdatum als Abgabefrist in der Übersicht geführt; auf der Wand wird der unbekannte Schluss benannt. Arbeitsblatt- und Quiztitel werden teilweise rein für die Anzeige ergänzt. Persönliche Aufgaben können ausdrücklich mit einem belegten Eintrag desselben Kurses verknüpft werden; bei übereinstimmender Aufgabenkennung folgt ihre angezeigte Frist dem aktuellen belegten Schluss, während der persönliche Beginn erhalten bleibt. Unklare oder widersprüchliche Verknüpfungen werden gekennzeichnet. Freitext-Verknüpfungen und zusätzliche Dringlichkeitsregeln sind noch nicht gleich. |
| Vergangene Lehreinheiten | Klar belegte Pflichtveranstaltungen werden nach ihrem letzten Tag lokal erledigt; die persönliche Wiederöffnung bleibt erhalten, eine geeignete künftige Datumskorrektur kann die automatische Änderung zurücknehmen. Erledigte Einträge bleiben im Kalender. Die Prüfung ist bewusst konservativ und erfasst nicht jede privat erkannte Veranstaltung. |
| Kalender und Timeline | Monats-, Wochen- und Ganttansicht und ein lokaler ICS-Download existieren. Der Export erhält bekannte Uhrzeiten, lokale Zeitzone und Enden; unbekannte Uhrzeiten und Dauern werden nicht erfunden. Die Timeline bietet 7/14/30 Tage voraus, Rückblick bis 30 Tage sowie Kurs- und Artfilter; erledigte vergangene Einträge bleiben dort lesbar. Die private Wochenrasterdarstellung und weitere Navigation sind nicht vollständig übertragen. |
| Quellen, Semester und Belege | Bei der Aufnahme wird ein Jahreswiderspruch zur wörtlichen Fundstelle abgewiesen. Ein ausdrücklich altes Semester wird bei eindeutig belegtem Kurssemester getrennt gespeichert und als unklar gekennzeichnet. Das Kurssemester wird nur aus einem eindeutigen Label im entdeckten Kurstitel übernommen; fehlt es, kann der Semesterschutz nicht greifen. Weitere private Semester- und Quellengültigkeitsprüfungen fehlen. |
| Dokumente und Quellenvergleich | Public besitzt Originale, Seitenbelege, OCR, explizite Analyse, Konflikte und versionierte persönliche Entscheidungen. Aufbau, Navigation und einige Vergleichs- und Leserfunktionen unterscheiden sich weiterhin. |
| Benachrichtigungen | Lesestatus ist getrennt vom Eintragsstatus gespeichert. Glocke mit Zähler sowie 90-Tage-Verlauf, Terminbündelung, Ungelesenfilter und 20er-Seiten sind vorhanden. Public öffnet eine eigene Seite; private Panel-Interaktion und Klangregeln sind noch nicht gleich. |
| Assistent und Sprache | Anbieterwahl, beleggebundene Antworten, Vorschau, UUID-Belege und Rückgängig bestehen. Mehrdeutige Rückfragen behalten ihre Auswahl und können durch ein bloßes Ja nicht übersprungen werden; nach einer Ja/Nein-Klärung bleibt die Vorschau erforderlich. Ausdrückliche Sammeländerungen für Status und Pins unterstützen Art-, Veranstaltungs-, Datums- und Statusfilter mit namentlicher Vorschau; bei über 100 Treffern muss enger gefiltert werden. Weitere private Bulkfilter, mehrstufige Dialogfälle und Sprachinteraktionen sind noch nicht vollständig übertragen. |
| Quests | Eigene Ziele, eine ausgewählte Aufgabe oder bis zu fünf automatisch gewählte offene Einträge sowie Dokumente können als versionierte, editierbare Entwürfe geplant werden. Gespeicherte Seiten einschließlich Leselücken und ausdrücklich angehängte PDFs werden einbezogen; Checklisten, Themenblöcke, Zeitbudgets, XP und rücknehmbare Fortschritte existieren. In gespeicherten HTML-Belegen verlinkte PDFs werden nur aus demselben lokalen Kursbestand zugeordnet; fehlende Dateien werden benannt und nicht abgerufen. Andere Verknüpfungsformen, alle privaten Ansichten und die vollständige fachliche Gleichheit der Modellplanung sind noch nicht nachgewiesen. |
| Google und lokale Integrationen | Der private Google-Kalenderabgleich und gehostete Tunnel fehlen. Die Übernahme braucht eigene Public-OAuth-Registrierung, sicheren Zugangsspeicher sowie freigegebene, duplikatsichere Schreib- und Konfliktbehandlung; die private Kontokonfiguration darf nicht übernommen werden. Lokales MCP und ICS ersetzen diese Funktionen nicht. Keine automatische TU-Synchronisation beim Start. |
| GitLab | Der Import aus eigenen GitLab-Bare-Caches einschließlich Commit- und Pfadbeleg ist in Public nicht implementiert. Der private Git-Aufruf verwendet vorhandene SSH-/Git-Konfiguration; Public benötigt dafür eine eigene sichere Zugangsanbindung und einen mit seinem gepinnten Netzwerkzugang verträglichen Transport. Ein vorhandener CSS-Selektor ist keine GitLab-Funktion. |
| Weitere Unterschiede | Zusätzliche Verlaufssichten, Quellenverwaltung und neuere Leistungs- und Lifecycle-Korrekturen wurden noch nicht vollständig funktional verglichen oder übertragen. |

Öffentliche Anbieter, eigene Zugangsspeicherung und Datenpfade, expliziter
manueller Quellenabruf und Public-Aktionsverträge bleiben eigenständig. Ein
synthetischer Test oder eine gerenderte Ansicht belegt weder ein reales Konto
noch vollständige Parität. Die beta.4-Installer werden separat gegen diese Änderungen gebaut und geprüft;
der jeweilige Prüfstand steht in RELEASE-VERIFICATION.md.
