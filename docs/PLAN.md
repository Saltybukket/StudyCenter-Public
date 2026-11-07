# Delivery plan

## Aktive Umsetzung: gemeinsame Grundlage (2026-11-06)

Ziel: dieselbe versionierte Produktfunktion fuer Private und Public, ein nachvollziehbarer
Windows/Linux-Auslieferungsweg und gemeinsame sichere Zustandsaenderungen. Keine
Neugestaltung der Oberflaeche oder Aenderung des Luna-Dialogverhaltens in diesem Schritt.

- [x] Ausgangsstand mit allen bestehenden Aenderungen extern sichern; privaten Produktstand pruefen und versionieren.
- [x] `scripts/sync_product.py` an unveraenderlichen Quell-Commit/Tree binden; Offline-CI und lokale Herkunftspruefung, fehlgeschlagene Updates ohne Teilzustand.
- [x] Einfache Distributionsunterschiede in explizite Einstellungen/Adapter verschieben; gemeinsame Quelldateien statt unabhaengiger Funktionskopien verwenden.
- [x] Windows/Linux-Workflow auf die aktuelle Slim-Auslieferung abstimmen; Herkunft und Paketpruefungen erhalten.
- [x] ICE-Import ueber installierten Einstieg und explizite Einrichtung erreichbar machen; nur neue isolierte Zielinstanz, keine Konto-/Profildaten uebernehmen.
- [x] Zusammenhaengende Aufgaben-/Statusaenderungen aus grossen Modulen in einen gemeinsamen Dienst auslagern; HTTP und Assistent nutzen dieselben Regeln.
- [x] Reihenfolge von Erledigen/Konfliktloesen, veraltete Aenderungen, Wiederholungen sowie Abgabe/Verfuegbarkeit/Bearbeitungstag gezielt pruefen.
- [x] Verbleibende Altmodule anhand echter Nutzer untersuchen; nur belegbar unbenutzten Code entfernen, kleine Architekturpruefung ergaenzen.
- [x] Gemeinsame Verhaltenstests, Public-Datenschutzpruefung, Quellabgleich und tatsaechliche Paketpruefungen durchfuehren; Grenzen getrennt dokumentieren.

Arbeitsteilung: getrennte Eigentumsbereiche fuer Quellabgleich, Release-Skripte,
ICE-Integration und private Zustandslogik. Integration und PLAN liegen beim Orchestrator.
Die Quelle wird erst nach erfolgreicher Integration erneut gebunden. Live-Daten,
Profile, Anmeldungen und laufende Installationen bleiben unberuehrt.

Pruefschwerpunkte: konkurrierende Aenderungen verlieren keinen persoenlichen Status;
Wiederholung erzeugt keine doppelten Aufgaben; historische Quellen werden nicht zu
aktuellen Fristen; Import ueberschreibt keinen vorhandenen Bestand; Paketpruefungen
verwenden die aktuelle Produktanwendung statt alter Datenvertraege.


### Lokaler Pruefstand 2026-11-06

Privater Produkt-Pin: `e78cfbbb00fb3d19b97abb1e3da78f5a60d9ce80`;
92 Quelldateien, davon 78 nach Textnormalisierung gemeinsam, 14 begruendete Adapter
und 5 Public-Ergaenzungen. Private Gesamtsuite: 446 bestanden. Public Gesamtsuite:
939 bestanden, 30 plattformbedingt uebersprungen. Ein danach aus Datenschutzgruenden
neutralisierter synthetischer Test-Port wurde mit 7 Desktop-Tests erneut geprueft.
Quell-/Arbeitsbaumabgleich, Architekturpruefung und Staging-Datenschutzcheck bestanden.
Das unabhaengige Review hat keine offenen konkreten Findings am integrierten Code.

Die private CI prueft bei Produktaenderungen den aktuellen Arbeitsstand gegen den
Public-Export und meldet ein Auseinanderlaufen. Die Public-CI prueft den fest gebundenen
Export ohne Zugang zum privaten Repository. Beide Pruefungen lesen nur Code.

Der abschliessende [GitHub-Paketlauf](https://github.com/Saltybukket/StudyCenter-Public/actions/runs/42222723656)
fuer `5d7b76f8d2451fc48c9c50effc0d630a85a0cab9` bestand auf Windows Server 2022
(940 Tests, 30 uebersprungen) und Ubuntu 24.04 (947 Tests, 23 uebersprungen).
Beide prueften das exakte Quell-Wheel, entpackte Komponenten, Upgrade von beta.5,
wiederherstellbare Sicherung mit Original/Beleg, installierten ICE-Import,
HTTP-Oberflaeche, PDF-Verarbeitung und optionale Komponenten mit Offline-Wiederholung.
Windows pruefte ausserdem unterbrochene Installation, fehlgeschlagenes Update,
Reparatur und datenbewahrende Deinstallation. Linux pruefte die private Python-Runtime;
unter Windows blieb System-Python ueber die Registry auffindbar, daher ist dort der
Fall ohne vorhandenes Python nur durch isolierte Auswahltests abgedeckt.

Der erste Lauf scheiterte an einer offenen SQLite-Verbindung der synthetischen
Fixture: spaeteres Schliessen checkpointete das WAL und aenderte die Hauptdateibytes.
Der Test schliesst Verbindungen nun deterministisch; alle strikten Bytevergleiche
bleiben erhalten. Regression und Nachreview bestaetigten die Gegenprobe mit echter
Datenaenderung. Der [private Quellabgleich](https://github.com/Saltybukket/CampusBoard/actions/runs/42221055708)
bestand ebenfalls. Keine Produktdatei wurde nach diesen Checks geaendert.

beta.6 bleibt ein Kandidat; der Workflow veroeffentlicht keine Artefakte und die
Downloadlinks bleiben bei beta.5. Live-TU-Konten, native Windows-11-Bedienung und
das bisher separat blockierte Linux-Browser-GUI-Gate wurden hier nicht geprueft.

## Gemeinsamer Produktstand und Auslieferung

Die einzige Produktquelle ist das private `campusboard/` an einem vollstaendigen
Git-Commit. Public enthaelt einen reproduzierbaren Export in `core/`, damit
Installation und CI keinen Zugang zum privaten Repository benoetigen. Sieben
zuvor angepasste Module (Browser, Dokumente, Google, iCal, Modelltransport, Mail,
Sync) verwenden jetzt dieselbe Quelle mit ausdruecklich uebergebenen Diensten.
Verbleibende Abweichungen stehen mit Grund in `packaging/product-adapters.json`;
Launcher, Speicher-/Betriebssystemintegration und Einrichtung bleiben getrennt.

`python scripts/sync_product.py verify` prueft lokal und in CI Inventar,
ausgelieferte Bytes, Adapter-Policy und rekonstruierte Git-Paket-Tree-ID.
`python scripts/sync_product.py verify --source-repo ../StudyCenter` prueft
zusaetzlich die Commit-zu-Tree-Bindung am privaten Git-Objekt. Mit
`--check-worktree` wird auch der aktive private Quell-Arbeitsstand gegen den Pin
geprueft, ohne Profil- oder Datenverzeichnisse zu betreten. Der Offline-Check
allein beweist diese Bindung und funktionale Gleichheit nicht. Aktualisierungen
verwenden `update --source-repo ... --revision <vollstaendiger Commit> --output
<neues Verzeichnis>` und erzeugen einen separaten Kandidaten; der laufende Export
wird dabei nicht teilweise ueberschrieben. Neue Dateien und Adapteraenderungen
muessen ausdruecklich geprueft und anschliessend mit `capture` neu gebunden werden.

`action_state.py` vereinheitlicht Zeit-/Fristprojektionen; `item_state.py` kapselt
persoenliche Aenderungen innerhalb der bestehenden SQLite-Transaktion. Formulare
uebergeben Feldrevisionen und explizite Ziele fuer Pin/Sichtbarkeit. Alte Clients
ohne Revision bleiben kompatibel, bieten aber keinen Schutz gegen veraltete
Formulare. Alte offene KI-Vorschauen koennen nach der Umstellung eine erneute
Vorschau benoetigen. Quellenkonflikt, Lesestand und persoenlicher Status bleiben
unabhaengig. Alte Backup-Schemata werden migriert; unbekannte Trigger nicht ausgefuehrt.

Vor Updates erstellen Windows-Installer und Linux-Starter mit der bisherigen
Python-Umgebung eine konsistente Core-Sicherung einschliesslich Dokumentoriginalen
und Belegen. Ein laufender Bestand oder Backupfehler blockiert den Wechsel. Der
CI-Lifecycle prueft neben Neuinstallation den Upgradepfad von beta.5 mit synthetischen
Daten und Fehlergegenproben. Native Windows-11-Bedienung und echte Kontenanmeldung
werden dadurch nicht belegt.

Die automatische Windows/Linux-Pruefung baut nun die Slim-Auslieferung aus dem
exakten Quell-Wheel. Optionale native Komponenten sind ueber die bereits
veroeffentlichten, geprueften Archive mit festen Hashes gebunden. Ein Wechsel
rotierender Upstream-Pakete lockert keine Herkunftspruefung. Alte monolithische
Build-/Smoke-Werkzeuge bleiben als historische Diagnose erhalten und sind kein
aktueller Release-Nachweis. Die manuellen Exact-Candidate-Workflows pruefen
zusaetzlich konkrete unveroeffentlichte Release-Artefakte.

`python scripts/check_product_architecture.py` verhindert Rueckimporte vom
Produktkern in die alte Public-Web-/Domain-/Storage-Schicht und prueft auch die
Python-Imports in Setup-Probes. Dokument- und Sprachinfrastruktur wird noch vom
Public-Adapter verwendet; Migration und Kompatibilitaetstests benoetigen weitere
Altmodule. Diese sind deshalb nicht pauschal als Dead Code entfernt. Die statische
Pruefung ersetzt keine Pruefung dynamischer Imports oder echter Kontenanbindung.

## Orientierung im Repository

| Bereich | Zweck |
| --- | --- |
| `src/studycenter_public/core/` | Gemeinsamer Produktkern einschließlich Oberfläche |
| `product.py`, `onboarding.py`, `migration.py`, `ice_migration.py` im Paket | Public-Einrichtung und Datenübernahme |
| `platform/`, `desktop/` im Paket | Eigene Datenpfade, Betriebssystem und Start |
| `collectors/`, `providers/`, `services/` im Paket | Wiederverwendete Infrastruktur und ältere Serviceverträge |
| `tests/`, `tests/product_reference/` | Public-Verträge und Verhalten des gemeinsamen Kerns |
| `scripts/`, `packaging/`, `.github/workflows/` | Prüfung, Quellabgleich und Auslieferung |
| `.local/`, `build/`, `dist/`, `reports-local/` | Lokale Daten, erzeugte Artefakte und Prüfbelege; nicht einchecken |

Der normale Einstieg ist `studycenter_public.product.create_app`. Die ältere
`web`-Anwendung und ihre Datenverträge werden noch von Kompatibilitätsprüfungen
verwendet; aktuelle Setup-Prüfungen verwenden den gemeinsamen Produkteinstieg.
Die Altmodule sind kein zweiter aktiver Produktkern.
Diese Module nicht allein wegen ihres Alters entfernen. Der Abgleich mit der privaten
Quelle bleibt über `scripts/sync_product.py verify` und das Quellmanifest prüfbar.

Pytest schreibt den Cache nach `.local/pytest-cache`. Zusätzliche Testausgaben gehören
in getrennte Verzeichnisse unter `.local/test-runs`, nicht neben den Quellcode.
Abgeschlossene Worktrees und Testserien werden nach Sicherung außerhalb des Repositories
archiviert. Offene Pull Requests und nicht übernommene Branches bleiben erhalten.

## Wechsel von ICE zu Public

Der Public-Quellstand bietet einen expliziten Offline-Import eines nativen
ICE-Format-1-Backups mit dem Datenbankschema von ICE beta.3. Der aktuelle Quellstand (Kandidat beta.6) bietet ihn auch unter Einrichtung
als expliziten ZIP-Import in einen neuen Geschwisterordner an. Er ist noch nicht
Bestandteil der veröffentlichten beta.5-Installer. Es gibt keine automatische
Suche nach ICE-Daten und keine Übernahme in einen bereits vorhandenen Public-Bestand.

1. In ICE eine aktuelle Sicherung erstellen. Zusätzlich das vollständige eigene
   ICE-Datenverzeichnis außerhalb des ICE-Programmordners aufbewahren. Die native
   ZIP-Sicherung enthält keine Einstellungen, Browserprofile oder Anmeldungen.
2. Im eingerichteten Public-Quellcheckout importieren; der Zielordner darf noch
   nicht existieren:

   ```text
   python -m studycenter_public --import-ice --backup "ICE-Backup.zip" --destination "../public-from-ice"
   python -m studycenter_public --data-dir "../public-from-ice" --browser
   ```

3. Aufgaben, Status, Notizen, Belege und Originale im neuen Bestand prüfen.
   TU-, Mail-, KI- und andere Konten bei Bedarf neu verbinden. Google-Spiegelung
   ist nach dem Import ausgeschaltet und braucht eine erneute Freigabe.

Der Import prüft Archivpfade, Schema, Datensatzanzahlen, Datenbankintegrität und
Dokument-Prüfsummen, bevor er den neuen Ordner atomar veröffentlicht. IDs,
persönliche Entscheidungen, Historien, Belege und Erinnerungszustände bleiben
erhalten. Das Quell-Backup bleibt unverändert. Der Beleg im neuen `core`-Ordner
enthält dessen SHA-256, Originalmanifest und die absichtlichen Transformationen.
Unbekannte Schemata, beschädigte oder zusätzliche nicht zugeordnete Originale,
mögliche Zugangsdaten und vorhandene Ziele führen zum Abbruch. Bei einem alten,
nicht unterstützten Schema zuerst mit der bisherigen ICE-Version eine neue
Sicherung erstellen; Original und Installation bis zum geprüften Umstieg behalten.

Vor einer Stilllegung außerdem Git-Historie einschließlich weiterer Branches,
Tags und offener PRs, Release-Dateien, Lizenzen und Prüfberichte außerhalb des
Repositories sichern und eine Wiederherstellung prüfen. Eine GitHub-Löschung
bricht bestehende ICE-Download- und Update-Links; Archivieren bewahrt sie.
Der offene ICE-PR #1 zu Abhängigkeiten ist nicht automatisch in Public enthalten
und bleibt separat zu bewerten. Eine Sicherung ist keine Freigabe zur Löschung.

## Historischer Auslieferungsplan

Target: v0.1.0-beta.2, AGPL-3.0-only, Windows x64 setup and Ubuntu 24.04 x64 package.

beta.2 simplifies the existing installation paths: official Microsoft prerequisite
acquisition from Windows Setup, one project-local developer starter and the Linux
system-browser starter. It also includes the background-job, dated-week and MCP
retry fixes. Its exact package checks are recorded in RELEASE-VERIFICATION.md.
The table below records the original beta.1 milestone delivery.

| Milestone | Scope | State |
|---|---|---|
| M0 | Independent repository, license, publication checks | delivered |
| M1 | Empty portable core, source facts, actions, secrets, backups | integrated; deletion ownership/backend countercontrols verified |
| M2 | Shared model contract and explicit account connections | integrated; synthetic protocol matrix verified |
| M3 | Manual source sync, own login, documents and optional mail | integrated; synthetic sources verified |
| M4 | Existing views, reader, comparison and text assistant | integrated; real local browser workflows verified |
| M5 | Reminders, optional speech and authenticated local MCP | integrated; controlled-clock/protocol checks and actual frozen UI reminder create/list/read verified |
| M6 | Windows/Linux packages and short onboarding | delivered; exact native artifacts and isolated Windows installer lifecycle verified |
| M7 | Independent review and real package/browser checks | scoped checks complete; CI and exact-package VM results recorded; personal acceptance separate |
| M8 | Human quickstart, verified artifacts and release | published v0.1.0-beta.1 with six checksum-bound assets; Linux source automation limitation explicit |

No external source write actions, automatic source jobs, hosted tunnels,
calendar cloud integration, mobile or macOS packages are in scope.
Tests use generated content. Account/hardware checks require the owner's
explicitly configured new installation and are reported separately.

## Verification boundaries

All fixtures are generated. Tests cover empty first use, foreign-directory refusal,
UUID retries, conflict-safe undo, course-bound source identities, versioned quotes,
personal decisions in every projection, read-only mail, sync exclusions/gaps,
bounded provider schemas, OAuth callback controls, backup/restore, reminders, MCP,
and explicit speech downloads. Independent local browser checks include actual
creation, comparison, original access, PDF reader/search, restore, and mobile layout.
A separate frozen Windows check created a task and personal reminder through the
UI, verified preview without mutation, observed the real-clock worker notification
in the app, listed it and marked it read. The task remained open. This does not
establish a visible operating-system notification or real-account acceptance.

The final native packages passed synthetic HTTP workflows, bundled OCR,
runtime imports, cooperative shutdown, inventory and frozen-code privacy checks.
Windows full Chromium passed visible and background modes. The exact installer
passed installation, update, backup, prerequisite refusal and data-retaining
uninstall in an ephemeral Windows test VM. Further dependency setups and uninstallers are
prohibited on the development host. Component builds extract checked archives into their
own directories and do not change system PATH, package databases or other apps.

Linux core/normal-system-browser UI and Playwright source automation have separate
acceptance gates. Ubuntu AppArmor can deny the bundled browsers; this does not
establish a core/UI failure or full Linux acceptance. Existing root-controlled
Chrome may use its already installed profile with an own Public user-data
directory. No policy exceptions, global restriction changes or sandbox bypasses
are part of this build. Actual VM results, including blocked automation, must be
reported independently of the test job's overall outcome.

Personal TU/MFA, live model/OAuth accounts, microphone/TTS and visible desktop
notifications remain separate user acceptance. WSL backend checks do not establish
a visible Linux desktop; server-runner checks do not establish Windows 10/11
interactive desktop acceptance. No paid model calls or model-weight downloads are
part of development verification.

Explicit final data removal requires provable ownership of every affected file
and an accessible OS credential store. Unknown nested files, browser/cache files
without a per-file ownership record, altered original hashes, links/reparse points,
or session-only credential mode block removal before any deletion. Save/move the
reported unverified files and reconnect the OS store before retrying. The instance
identity is retained when preflight fails; uninstall always retains personal data.

## Published delivery

[Beta release](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.1)
was published on 2026-10-15. [Release verification](RELEASE-VERIFICATION.md)
records the exact artifacts, actual CI and VM runs, and remaining personal
acceptance. Linux normal-system-browser UI passed; automated source browsing
remains blocked in the Ubuntu VM by the existing browser ownership check.

## Produktabgleich: Upcoming und kompaktes Luna-Fenster (2026-11-07)

Der gemeinsame Kern ist auf Referenzcommit
`1ea3719dc78cff73bd2af04e8fa02f2dea40c88e` aktualisiert. Enthalten sind Upcoming
mit sieben Kalendertagen und getrennten alten Aufgaben/Quest-Schritten, das
kompakte nichtmodale Luna-Fenster, Kurspriorisierung nach belegten Fristen,
direkte Material-/Quest-Zugaenge, getrennte zurueckgestellte Medien, lesbare
Kalenderstatus und kompakte Kopfzeilen. Die Post-it-Wand bleibt unveraendert.

Uebernommen werden ausschliesslich Produktcode, lokale Standardassets und
synthetische Tests. Public-Einrichtung, Instanzpfade, Credential-Namespace und
Onboarding bleiben als Distributionsanpassungen erhalten. Keine privaten
Datenbanken, Quellenmaterialien, Browserprofile, Berichte oder Zugangsdaten
gehoeren zum Export. Der Quellabgleich prueft Commit, Baum, Arbeitskopie und
ausgelieferte Dateihashes; Verhalten und Darstellung werden davon unabhaengig
mit isolierten synthetischen Daten geprueft. Dieser Quellabgleich veroeffentlicht
keine neuen Installer und startet keine Synchronisierung.

Validierung: 44 gezielte Produkt-/Runtime-Tests ohne uebersprungene Browserfaelle
und 24 Tests fuer Distributionsisolation, Quellbindung, Architektur und Onboarding
bestanden. Headless-Browser mit frischem temporaerem Profil; keine eigenen
Konten oder echten Modellanfragen. Vergleich mit identischen synthetischen
Daten bei 1440x900 und 390x900 fuer Upcoming, Kurse, Dokumente, Kalender,
Quests und Luna: gleiche sichtbare Inhalte; 11 von 12 Bildpaaren pixelgleich.
Das mobile Luna-Bild weicht an einem Pixel ab, ohne sichtbaren Layoutunterschied.
Betriebssystem-Installer wurden fuer diesen Quellabgleich nicht neu gebaut
oder auf Linux/Windows installiert.
