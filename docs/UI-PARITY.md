# Oberfläche und Funktionsunterschiede

Verglichen wurden die tatsächlichen Templates, Styles und Skripte der privaten
Referenz und von Public. Die private Anwendung, Konten und Daten wurden nicht
verwendet. Screenshots entstanden aus synthetischen Testdaten; Referenzansichten
wurden separat aus den gelesenen Templates gerendert.

## Vorhandene Funktionen mit angeglichener Oberfläche

| Bereich | Umsetzung in Public |
| --- | --- |
| Einstellungen | Sechs Register: Benachrichtigungen, Sprache, Darstellung, Verbindungen, Sicherungen und Betrieb. URL-Fragmente, Browserhistorie, Tastatur, Theme-Karten und mobile Darstellung. Vorhandene Formulare, Status- und Fehlermeldungen bleiben erhalten. |
| Navigation | Getrennte Studien- und Verwaltungsnavigation, Suche, kompakter Seitenkopf und vertraute Karten. Public-spezifische Pins, Assistent und Einrichtung bleiben erreichbar. |
| Übersicht | Zwei datumsbasierte Bereiche für offene Fristen/Termine einschließlich überfälliger Einträge und die nächsten sieben Tage; Quellen und Dokumente separat. Keine unbelegte Dringlichkeitsbewertung. |
| Aufgaben/Post-its | Angeheftet, persönlich geplant und offen; Statuswechsel, Pin und Details über vorhandene Vorschau, Ausführungsbeleg und Rückgängig. |
| Kalender | Bestehende Monats-, Wochen- und Gantt-Ansicht mit angeglichener Navigation; keine Änderung an den gespeicherten Terminen. |
| Dokumente, Quellen, KI | Dokumenttabelle, Quellenkarten, Status und Formulardetails angeglichen. Originale, Lesefassungen, OCR, manueller Abruf, Kursauswahl und Providerwahl bleiben erhalten. |
| Kleine Funktionslücken | Unterstützte Schlussdatum-/Schlusszeit-Felder sind nun in Anlegen/Bearbeiten sichtbar. Persönliche Erinnerungen zeigen gespeicherte oder geerbte Werte. Bearbeiten übermittelt nur geänderte Felder; unberührte Quellwerte bleiben frei für spätere Aktualisierungen. |

Windows-Tray-Einstellungen sind unter Linux ausdrücklich nicht verfügbar.
Der globale Kompaktmodus, Direktmodus, lokale MCP-Rechte, Sprachmodelle,
Komponentenstatus, Sicherung/Wiederherstellung und eigene Datenlöschung sind
weiter erreichbar. Fehlende Komponenten werden nicht durch die UI-Angleichung
automatisch installiert.

## Tatsächlich verbleibende Unterschiede

Diese Unterschiede sind keine pauschale Zusage, jede private Zusatzfunktion
nachzubauen. Die vereinbarten Public-Kernfunktionen bleiben vorhanden.

- Die private Referenz besitzt zusätzliche Dringlichkeitsregeln, automatische
  Pins und eigene Neu-/Änderungs-/Verlaufsansichten. Public behält manuelle Pins
  und eine transparente datumsbasierte Übersicht.
- Kombinierte Kurs-/Art-/Statusfilter und die zeitgenaue Wochenrasteransicht sind
  in Public einfacher. Der vorhandene ICS-Export verwendet ganztägige Werte;
  Zeit-/Bereichsfilter wären eigenständige funktionale Erweiterungen.
- Der Dokumentleser und Quellenvergleich bleiben funktional Public-eigenständig:
  Originale, Seitenbelege, Konflikte und persönliche Entscheidungen bleiben
  erhalten. Ein dauerhafter „Zur Quelle zurücksetzen“-Befehl ist nicht vorhanden;
  die letzte persönliche Änderung kann über ihren Beleg rückgängig gemacht werden.
- Klangregeln und Sondervorläufe für ungenaue Termine sind in der privaten
  Referenz umfangreicher. Public behält seine geprüften Erinnerungsregeln und
  erfindet keine Uhrzeiten.

## Bewusst später

Google Kalender und gehostetes MCP/Tunnel gehören nicht zu diesem Public-Schritt.
Es gibt dafür keine funktionslosen Schalter. Der authentifizierte lokale
MCP-Zugang und der lokale ICS-Download bleiben nutzbar.

Die Anpassung verändert weder beta.3-Installer noch Paketverwaltung.
Persönliche Kurse, Anbieterprofile, Zugänge und private Installationspfade sind
kein Bestandteil der übernommenen Gestaltung.
