# Release verification record

This record covers the `0.1.0b1` candidate. Final native, installer and CI
acceptance remains open. A source or notice check alone does not approve a
binary distribution.

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

Earlier native candidates are held and are not the final release. The final
runtime build source will be recorded after the integration checkpoint.
Each final binary must retain its
actual `BUILD-MANIFEST.json` source identity and file hashes. A later release tag
may be a descendant only with evidence that its selected runtime and packaging
files match the build. A later tag must not relabel existing binary provenance.

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
and notice bindings. Widevine and the unused standalone video helper are
excluded. Windows shader compiler notices bind the exact Chromium/Dawn/DXC
chain. WebView2 SDK 1.0.3856.49 retains its own Microsoft BSD-3-Clause license
and NOTICE; its Evergreen browser runtime is separate.

This Windows candidate excludes Microsoft CRT/UCRT, API-set forwarding DLLs
and Windows SDK D3DCompiler binaries. Compatible installed x64 system components
are prerequisites. `SYSTEM-RUNTIME-RECIPE.json` records omissions and the exact
NumPy consumer import-name changes. No renamed Microsoft DLL copies or runtime
installer are shipped. A future one-click prerequisite flow needs a verified
official, license-compliant Redistributable route; no host setup or Visual
Studio entitlement is implied. Shader build documents are not SDK shipment grants.

Final package notice validation and exact shipment matching remain required.
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

## Final acceptance fields

| Check | Status | Required final evidence |
| --- | --- | --- |
| Windows native runtime and sandboxed browsers | OPEN | Exact package hashes and native results |
| Linux core and normal system-browser UI | OPEN | Exact package hashes; separate core and own XDG/Xvfb browser results |
| Linux Playwright source browser | OPEN | Existing regular Chrome/profile and own renderer sandbox results; bundled automation may remain blocked |
| Windows install/update/uninstall and prerequisite refusal | OPEN | Isolated VM lifecycle results |
| Final CI run | OPEN | Run identity, source identity and results |
| Final notices and unpacked binary privacy gates | OPEN | Validators against the final distributions |
| Release tag/build association and publication | OPEN | Selected-file identity proof and final checksums |

The existing-system-browser VM probe has separate UI and synthetic-source
statuses. Its successful transport exit alone does not accept either lane or
the whole Linux release. No AppArmor exception or sandbox bypass is included.
