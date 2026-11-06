# Delivery plan

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
und bestehenden Setup-Prüfungen verwendet; sie sind kein zweiter aktiver Produktkern.
Diese Module nicht allein wegen ihres Alters entfernen. Der Abgleich mit der privaten
Quelle bleibt über `scripts/sync_product.py verify` und das Quellmanifest prüfbar.

Pytest schreibt den Cache nach `.local/pytest-cache`. Zusätzliche Testausgaben gehören
in getrennte Verzeichnisse unter `.local/test-runs`, nicht neben den Quellcode.
Abgeschlossene Worktrees und Testserien werden nach Sicherung außerhalb des Repositories
archiviert. Offene Pull Requests und nicht übernommene Branches bleiben erhalten.

## Wechsel von ICE zu Public

Der Public-Quellstand bietet einen expliziten Offline-Import eines nativen
ICE-Format-1-Backups mit dem Datenbankschema von ICE beta.3. Der Import ist noch
nicht Bestandteil der veröffentlichten beta.5-Installer. Es gibt keine automatische
Suche nach ICE-Daten und keine Übernahme in einen bereits vorhandenen Public-Bestand.

1. In ICE eine aktuelle Sicherung erstellen. Zusätzlich das vollständige eigene
   ICE-Datenverzeichnis außerhalb des ICE-Programmordners aufbewahren. Die native
   ZIP-Sicherung enthält keine Einstellungen, Browserprofile oder Anmeldungen.
2. Im eingerichteten Public-Quellcheckout importieren; der Zielordner darf noch
   nicht existieren:

   ```text
   python scripts/import_ice.py --backup "ICE-Backup.zip" --destination "../public-from-ice"
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
