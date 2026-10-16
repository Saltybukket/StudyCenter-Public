# Release verification record

This record covers the published [v0.1.0-beta.1 prerelease](https://github.com/Saltybukket/StudyCenter-Public/releases/tag/v0.1.0-beta.1),
published on 2026-10-15. The native, installer and privacy results below apply
to the exact release assets. Personal account and interactive desktop acceptance
remain separate; Linux automated source browsing is explicitly blocked.

## Artifact and source binding

The dependency companion is
`StudyCenter-Public-corresponding-sources-0.1.0b1-all-platform-notices.zip`:

- Size: `791589779` bytes.
- SHA256: `02f56b5a0f88582391bf83d3ded9219bdae56b7247578a18965d906bfffebe0d`.
- Manifest: 761 files; 160 unchanged primary source archives verified against
  their recorded origins, sizes and hashes.
- Original notices include 11 refreshed browser files, 22 shader-component
  texts and four WebView2 SDK notice/provenance files.

The companion's `PUBLIC-SOURCE-RECEIPTS.json`, `SOURCE-NOTICES-UPDATE.json` and
source-correspondence records identify exact upstream versions, source trees,
recipes and patches. Immutable-origin hashes and independently published recipe
checksum matches are distinguished. Collection did not execute build recipes
or reproduce upstream binaries; supplied Debian signatures were not GPG-verified.

Earlier native candidates are held and are not the final release. The current
runtime build source is `8d8c25d8b93c22abee061c755dc0816b44db84f3`. Its raw
source archive `studycenter-public-0.1.0b1-source-8d8c25d-rawblobs.zip` is
411616 bytes, SHA256
`5f9aa0318c6b96f32a11dbf74939ddbd29d4e702c25d598c49be2c842811bc4c`.
All 141 regular members, Git modes, tree membership, archive comment and raw
Git blobs were checked. The source privacy and artifact checks had zero findings.
The unchanged companion whole-file hash was refreshed; earlier recursive
content checks are inherited through that exact hash, not repeated.

The current Windows build manifest SHA256 is
`e48a04bcd6632a0e49d1626f512871009d62aa60e08aa1286bc9a7e6f6864e0b`;
the Linux build manifest SHA256 is
`fea59049d0931c43a9e255242072872fd0da4f56faf80c6022e6fb0012b802f1`.
Each binds exactly 62 runtime sources and 19 packaging definitions to this
commit. Windows has 63 raw-byte matches, ten exact LF-to-CRLF projections and
eight projections changing only the terminal LF to CRLF. Linux has 79 raw-byte
matches and two exact LF-to-CRLF projections. The terminal-only Python
representations retain identical parsed syntax. These named representations
do not make the physical checkout bytes identical to the raw Git blobs.
Source binding is separate from the payload and installer checks below.
The published tag points to `b4aae7e8e9ef4a7a7f500fdbc4e07dbf6ad542e0`.
Its four changed paths contain only the installer test controller, its tests,
and an exact upstream CI privacy classification with its tests. All 81 selected
application and packaging files have identical raw Git blobs and modes to the
actual build source. The binaries retain their original build identity; the
source archive contains that actual build source, rather than relabelling it.

Local frozen interpreters are Windows CPython 3.12.6 and Linux CPython 3.12.3
with Ubuntu source package `3.12.3-1ubuntu0.17`; corresponding sources and distro
patches are retained. CI Python 3.12.10/3.12.15 are test environments, not these
local release interpreters.

## License scope

Original component terms remain applicable. The application and PyMuPDF use
their AGPL terms. PyAV's exact vendor recipe includes FFmpeg, x264 and x265;
the shipment must not be described as LGPL-only. GNU/MPL covered sources,
patches and original notices accompany the dependency sources. PyInstaller's
bootloader exception, CPython notices and Inno Setup terms are retained.

Full unbranded Chromium and the dedicated Headless Shell have separate source
and notice bindings. Windows uses full unbranded Chromium for both visible and
background modes and excludes the unused Windows Headless Shell. Linux retains
its separately selected source-browser engines. Widevine and the unused standalone video helper are
excluded. Windows shader compiler notices bind the exact Chromium/Dawn/DXC
chain. WebView2 SDK 1.0.3856.49 retains its own Microsoft BSD-3-Clause license
and NOTICE; its Evergreen browser runtime is separate.

This Windows release excludes Microsoft CRT/UCRT, API-set forwarding DLLs
and Windows SDK D3DCompiler binaries. Compatible installed x64 system components
are prerequisites. `SYSTEM-RUNTIME-RECIPE.json` records omissions and the exact
NumPy consumer import-name changes. No renamed Microsoft DLL copies or runtime
installer are shipped. A future one-click prerequisite flow needs a verified
official, license-compliant Redistributable route; no host setup or Visual
Studio entitlement is implied. Shader build documents are not SDK shipment grants.

Final package validation matched 488 notice records and 589 provenance records
to the exact shipments. Both source manifests and their declared components
were validated.
These provenance checks are not a legal certification. See [component terms](THIRD_PARTY.md)
and [packaging requirements](PACKAGING.md).

## Source privacy scope and limits

The recursive review read 268968 regular streams with bounded decoders and
byte/hash inventories. It did not follow 1279 links or open nine special
members. Public-rule findings were individually classified against unchanged
primary upstream members: 1108 classified member findings, zero unresolved;
supplied private-indicator checks found zero matches. No upstream-wide exemption
was used.

A separate ASCII and UTF-16 check for the added DSA/encrypted PEM indicators
covered every inventoried regular stream: 69 findings individually classified,
zero unresolved. Unchanged archive hashes bind these earlier content checks to
this companion. The 29 new or modified text entries were separately checked
against public rules, private indicators and the added PEM indicators, with
zero findings or read limits.

An additional targeted decode of 20 RPM and one DEB upstream test fixtures
read 58 inner regular members through bounded AR/RPM/CPIO decoders. These inner
streams had zero findings and zero format limits. Three deliberately malformed
CPython `Lib/test/recursion.tar` fixtures remain unparseable: their original
516-byte contents were checked and hash-bound, but no inner-member PASS is
claimed. Encrypted test data was not decrypted. Source distributions include
original upstream binary build tools and fixtures; static inspection does not
prove executable behavior or self-extractor contents. No source package was executed.

## Short onboarding and local UI evidence

The README has 383 whitespace-separated words and the Quickstart has 496.
The Quickstart in each current native package is an exact 4279-byte match to
both the documentation and embedded source resource, SHA256
`fa4aeca9ec606c8125784be3263fc9e4eee3e0341ec26d982c9d35d98617a798`.
UI labels, starter instructions and current package filenames were checked.
Windows system prerequisites and the Linux UI/source-automation distinction
remain explicit.

One separate frozen Windows UI check used a new synthetic local task, the
current real clock and an isolated app/profile. It verified task creation,
reminder preview without mutation, apply, worker delivery to the in-app bell,
listing and mark-read. The task remained open and its version unchanged.
The complete program inventory remained unchanged after a three-second late
check. No model call, real account, OS notification or human visibility was
tested in that lane.

## Final artifact and platform results

| Check | Result | Scope |
| --- | --- | --- |
| Windows native runtime | PASS | Four complete synthetic runs, including Unicode and long data paths; 13 controls per run, own Chromium, unchanged program inventory |
| Windows exact installer | PASS | Install, foreign-directory refusal, prerequisites, update with exact database preservation and backup, reopen, data-retaining uninstall and own shortcut removal |
| Linux native core | PASS | One accepted serial full 13-control WSL run as an unprivileged user, portable OCR, both own browser engines and unchanged program inventory |
| Linux normal system-browser UI | PASS | Exact package, own XDG dispatch, overview and PDF reader rendered with Xvfb; six renderer sandbox states checked |
| Linux automated source browser | BLOCKED | Existing Ubuntu VM Chrome directories have mode 0777; production ownership checks refuse them before launch |
| Final CI | PASS with separate Linux browser limitation | Both required jobs completed; Linux bundled-browser probe remains blocked by existing sandbox restrictions |
| Final unpacked privacy and notices | PASS | Exact upstream findings individually bound; zero unresolved; linked members handled separately |
| Publication and source identity | PASS | Six uploaded assets, server SHA256 values, actual source and tag association verified |

The exact [Windows installer VM run](https://github.com/Saltybukket/StudyCenter-Public/actions/runs/38721023423)
performed two installed full workflows. It retained instance identity, compared
database bytes after application readiness, backed up before update, reopened
the existing data and retained data on uninstall. Four foreign-data startup modes
refused without writes. Missing-runtime and missing-export prerequisite fixtures
were rejected inside that disposable VM. This is not Windows 10/11 human desktop
acceptance.

The exact [Linux system-browser VM run](https://github.com/Saltybukket/StudyCenter-Public/actions/runs/38722260734)
used its existing Chrome 154 and existing AppArmor profile. UI rendering passed;
automated source browsing did not start. Existing browser bytes, confinement
policy and global restrictions remained unchanged. No AppArmor exception or
sandbox bypass is included. WSL native checks and Xvfb UI checks do not establish
a human desktop session or real TU login.

[Final CI run](https://github.com/Saltybukket/StudyCenter-Public/actions/runs/38723573578)
tested the exact tag commit: Windows 460 passed / 28 skipped; Linux 470 passed /
18 skipped. Windows completed the installer lifecycle and final privacy scan.
Linux completed core, frozen-code and privacy gates. Its bundled-browser probe
failed under the runner's unchanged user-namespace restriction and was reported
separately; a green required-job result does not convert that probe into a PASS.
CI builds are diagnostics, not the uploaded native release artifacts.

The Windows release contains 2405 manifest payload files plus the manifest.
Its completed raw scan checked 2525 chunks: 20 raw findings, 20 exact reviewed
upstream findings, zero unresolved. The installer was decoded without running
Setup on the development host; all 2406 decoded regular files matched the scanned
bundle by size and SHA256, with no extra files or links. This exact byte identity
inherits the bundle scan; it is not a fresh decoded-content scan. The installer
outer-byte scan also had zero findings.

The Linux tar scan checked all 3158 regular files in 3328 chunks: 32 raw findings,
32 exact reviewed upstream findings, zero unresolved. All 54 relative symlinks
were inventoried without following them and bound to the typed tar headers,
manifest, native inventory and exact internal regular targets. No hard links,
sparse members or special members were accepted. Release frozen-code scans
checked 2303 Windows and 2177 Linux modules with zero unresolved findings.

| Published binary | Bytes | SHA256 |
| --- | --- | --- |
| `studycenter-public-0.1.0b1-windows-x86_64-setup.exe` | 338913297 | `87ebaf625aa50a513b95c6e3826a7b7c125835806e659287b07bccc50bca389c` |
| `studycenter-public-0.1.0b1-linux-x86_64.tar.gz` | 714974815 | `3fbdb322e8153f669edcf1a66570bc8ee6d60b2223b6daa1c9735127f0a58484` |

The release also includes the exact raw build-source ZIP, dependency companion,
application LICENSE and SHA256SUMS. All six server digests and uploaded states
were verified after publication. Checksums cover the five accompanying files.

TU/MFA and live provider/OAuth connections, microphone/TTS, visible operating-system
notifications and human Windows 10/11 desktop acceptance remain unrun. Optional
speech models were not downloaded, and no paid model calls were made. Windows
requires compatible existing x64 system runtimes; this build does not install
them. The separate Linux automated-browser blocker remains open.

## Additional Windows 11 VM checks (2026-10-15)

The published beta.1 installer was subsequently tested in a dedicated Windows 11
Enterprise Evaluation VM, measured as build 26300.9457, with VirtualBox 7.2.18,
4096 MiB RAM and three vCPUs. Shared folders, clipboard and drag-and-drop were
disabled. These checks do not establish native desktop or human acceptance.

On the fresh guest, all four required Visual C++ runtime DLLs were absent. Setup
correctly refused with exit 7, creating no program/data directory or uninstall
registration. The official Microsoft-signed x64 v14 runtime 14.51.36247.0 was then
installed only inside the guest, followed by the checksum-bound published setup.
This does not change beta.1's external runtime prerequisite or redistribution.

The actual installed executable passed all five lifecycle checks: empty-instance
health; synthetic API items, document, UUID retry and initialized FTS backup;
reinstall against the running instance with byte-identical data and a real
before-update backup; reopening with retained state; and uninstall removing the
program, per-user registration and Start menu group while retaining database,
identity and original bytes. The published setup was installed again afterwards.

With the VM network cable disconnected, the broader installed-app suite reported
14 PASS and three FAIL. Its bundled Chromium retained its sandbox. Passed checks
include actual UI actions and conflict-safe undo, PDF/text/scan and bitmap-grid
OCR, vector-grid/table/strikeout fixtures, reader revisions, browser boundaries
and unchanged installed program files. The remaining results require care:

- The reminder check reported an early notification. Its first-run diagnostics
  were insufficient to distinguish a product defect from guest clock behavior;
  this remains an unresolved RED.
- Backup comparison incorrectly required unchanged physical storage paths across
  a portable restore. MCP retry comparison incorrectly required JSON key order.
  A separate stricter semantic probe is prepared, but has not been run; neither
  initial FAIL is relabeled as PASS.
- The requested week route rendered the month grid. Consented document extraction
  also reproduced HTTP 500 with `RuntimeError: no running event loop`.

A separate synthetic local model-adapter suite passed ten installed-app checks
for assistant preview/apply/undo, explicit single-action direct mode, ambiguous,
multiple and question refusal, acknowledgement deduplication and source-decision
refusal. This is not live model, provider-account or institutional acceptance.

Source commit `25e9057e92eaf0db27dcca59ec774fd930542eb9` fixes the background-job
dispatch and adds a dated Monday-to-Sunday week calendar. HTTP regressions
reproduced the defects before the changes. The complete Windows source suite then
reported 469 passed and 28 skipped; independent scoped review found no findings.

A separate local Windows test candidate was built from that exact commit:
338916793 bytes, SHA256
`fad19f5f71cb749fc5850daa3eda682a91a5e197ef451dba8aaac2a9dd7c5b58`.
It retains the development version string `0.1.0b1`; it is not a new published
release and does not replace any beta.1 asset. Its bundle scan checked 2525
chunks, with 20 exact reviewed upstream findings and zero unresolved. Frozen-code
inspection checked 2303 modules with zero unresolved findings. Decoder-only
inspection matched all 2406 payload files to the build manifest; decoded metadata
and installer outer-byte checks also had zero unresolved findings. The frozen
async handlers and copied calendar assets were separately bound to the source.

The VM was safely saved after sustained host memory pressure. Candidate guest
retests are blocked on available RAM. Native window/tray/restart checks, the
registration-opening suite, full Windows Update completion and the improved
reminder/backup/MCP probes remain unperformed. Clean and beta.1 test-baseline
snapshots are retained. No full Windows 11 or candidate acceptance is claimed.

## beta.2 installation simplification (2026-10-16)

Build `62b235eedae220823229e10823d933b6718ccf63` passed 476 Windows tests (28
skipped) and 484 Linux tests (20 skipped). Its exact Windows setup passed the
existing disposable-runner install/update/backup/uninstall checks. Linux core,
HTTP UI and PDF/OCR checks passed; its separate automated-browser probe remained
blocked by AppArmor. Unpacked and frozen-code scans had no unresolved findings.
The fresh Windows developer starter actually downloaded its own Python,
dependencies, browser and OCR, then started offline with seven week days and
stopped normally. No host dependency installer or Windows 11 VM was run. The
new Microsoft prerequisite acquisition compiled and retains its visible license
and UAC interaction; that missing-runtime UI sequence was not manually exercised.
Native Windows 11 and live-account acceptance remain separate. The explicitly
approved temporary draft-upload path is removed from the ordinary build workflow.
