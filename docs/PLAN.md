# Delivery plan

Target: v0.1.0-beta.1, AGPL-3.0-only, Windows x64 setup and Ubuntu 24.04 x64 package.

| Milestone | Scope | State |
|---|---|---|
| M0 | Independent repository, license, publication checks | delivered |
| M1 | Empty portable core, source facts, actions, secrets, backups | integrated; deletion ownership/backend countercontrols verified |
| M2 | Shared model contract and explicit account connections | integrated; synthetic protocol matrix verified |
| M3 | Manual source sync, own login, documents and optional mail | integrated; synthetic sources verified |
| M4 | Existing views, reader, comparison and text assistant | integrated; real local browser workflows verified |
| M5 | Reminders, optional speech and authenticated local MCP | integrated; controlled-clock and protocol checks verified |
| M6 | Windows/Linux packages and short onboarding | native candidate builds; final source checkpoint pending |
| M7 | Independent review and real package/browser checks | active; final artifact and installer lifecycle checks pending |
| M8 | Human quickstart, verified artifacts and release | documentation prepared; publication gated by final checks |

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

Final native packages must pass the same synthetic HTTP workflow, bundled OCR,
Windows Chromium modes, runtime imports, cooperative shutdown, file checksums and
decompressed frozen-code privacy checks. Windows installer lifecycle tests belong
in an ephemeral Windows test VM. Further dependency setups and uninstallers are
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
