# StudyCenter Public – Schnellstart

Aufgaben, Notizen, Kalender und PDF-Import funktionieren ohne Konto. Persönliche Daten liegen im eigenen lokalen Bestand.

Diese Anleitung beschreibt beta.3 Slim. Unter [Releases und Prüfsummen](https://github.com/Saltybukket/StudyCenter-Public/releases) stehen die veröffentlichten Versionen: für Windows die als empfohlen bezeichnete Setup-Datei, für Linux das Slim-Archiv wählen. Das weiterhin verfügbare beta.2-Komplettpaket enthält seine Komponenten bereits.

## Windows 10/11 x64

Setup herunterladen, öffnen und Programm- sowie Datenordner bestätigen. Falls eine Microsoft-Laufzeit fehlt, den angebotenen offiziellen Download und die sichtbaren Microsoft-Dialoge abschließen. Anschließend **StudyCenter-Public** über das Startmenü öffnen.

Der Slim-Installer benötigt weder Git noch vorinstalliertes Python. Beim ersten Aufbau lädt er die Basis in eine eigene Umgebung. Geeignetes vorhandenes CPython 3.12 x64 dient nur als Interpreter; sonst wird eine eigene Laufzeit eingerichtet. Globale Python-Pakete bleiben unverändert.

## Linux x64

Das passende Archiv entpacken, im Anwendungsordner ein Terminal öffnen und ausführen:

```sh
sh start.sh
```

Die Oberfläche öffnet sich im Systembrowser. Ersteinrichtung und gewünschte Komponenten benötigen Internet. Spätere Starts verwenden die vorbereitete Umgebung; lokale Funktionen sind dann offline nutzbar.

Zum Beenden `sh start.sh --stop` verwenden. Bei abweichendem Datenordner für Start und Stop dieselbe Option `--data-dir "/dein/Ordner"` angeben. Unter Windows gibt es **Vollständig beenden** im Tray beziehungsweise in der Oberfläche.

## Erster Start und freiwillige Verbindungen

1. Unter **Einrichtung → Speicher** den eigenen Datenort prüfen.
2. Eine Aufgabe, Notiz oder PDF anlegen. Anzeige und native Text-PDF-Verarbeitung benötigen keine OCR.
3. **Kurse & Quellen:** bei Bedarf den TU-Quellenbrowser einrichten, selbst anmelden, Kurse auswählen und **Jetzt synchronisieren** drücken. Es gibt keinen automatischen Quellenabgleich.
4. **KI-Verbindung:** freiwillig eigenen Anbieter oder vorhandenen lokalen Modellserver wählen. Cloud-Aktionen können Kosten verursachen; Vorschläge vor **Übernehmen** prüfen.
5. **Einstellungen:** OCR oder Sprache erst bei Bedarf einrichten. Sprachbibliotheken und Modellgewichte sind getrennte Downloads; Gewichte erst nach Größenanzeige bestätigen.

## Häufige Probleme

- **Download abgebrochen:** Verbindung prüfen und dieselbe Einrichtung erneut versuchen; keine fremden Ordner löschen.
- **Python später entfernt:** die angebotene Reparatur mit eigener Laufzeit verwenden.
- **PDF enthält keinen Text:** Original ansehen und OCR ausdrücklich einrichten; Leselücken bleiben möglich.
- **TU-Browser unter Linux blockiert:** lokale Funktionen weiterverwenden. AppArmor kann den getrennten Quellenbrowser einschränken; Sicherheitsregeln eingeschaltet lassen.
- **TU-/KI-Zugang fehlt:** eigene Anmeldung beziehungsweise Anbieter und Modell kontrollieren. Ein KI-Verbindungstest kann kostenpflichtig sein.

Updates bewusst auslösen. Vor dem Wechsel von beta.2 den bisherigen eigenen Datenordner prüfen. Deinstallation erhält persönliche Daten standardmäßig. Sicherungen können private Studieninhalte enthalten und gehören nicht ins öffentliche Repository.

[Aus Git starten und entwickeln](https://github.com/Saltybukket/StudyCenter-Public/blob/main/docs/DEVELOPMENT.md) · [Prüfstand und bekannte Grenzen](https://github.com/Saltybukket/StudyCenter-Public/blob/main/docs/RELEASE-VERIFICATION.md)
