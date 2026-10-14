# Native release builds

The release consists of a Windows x86-64 per-user Inno Setup installer and a
Linux x86-64 tar.gz directory. Each contains its own Python runtime, local web
assets, Playwright Node driver, visible Chromium, dedicated Chromium Headless
Shell, PDF support, Tesseract with English/German data, and speech libraries.
Speech model weights are downloaded only after an explicit user choice.

Windows supports Windows 10/11. The embedded window uses the Microsoft WebView2
runtime; when it is unavailable, the application opens its local URL in the
user's browser. Linux builds target Ubuntu 24.04 and newer compatible glibc
systems. The runtime does not require a separately installed Python or OCR
package. A system desktop/browser is required for graphical use; headless
operation supports `--no-browser`.

Build on each target OS with Python 3.12 and the project's locked dependencies
in a dedicated virtual environment. Build directories and downloads remain
outside version control. Do not execute dependency installers to extract files:
use archive extraction and copy only the component runtime. Never reuse another
application's environment, data or browser profile.

```powershell
$env:PLAYWRIGHT_BROWSERS_PATH = "$PWD/build/browser-runtime/windows"
.venv/Scripts/python -m playwright install chromium
.venv/Scripts/python scripts/build_browser_runtime.py --platform windows --original build/browser-runtime/windows --output build/unbranded-browser-runtime/windows
.venv/Scripts/python scripts/build_release.py --ocr-dir build/windows-ocr/runtime --browsers-dir build/unbranded-browser-runtime/windows/runtime --iscc build/tools/inno/ISCC.exe
```

The Windows OCR input is a portable extracted tree containing `tesseract.exe`,
its runtime DLLs, `tessdata/eng.traineddata`, `tessdata/deu.traineddata`, and
notices. The builder recursively copies imported runtime DLLs and excludes
installers, uninstallers, training tools and machine configuration. Inno Setup
is a build-time compiler; it is not included in the application.

Create a fresh Windows OCR tree with `python scripts/build_windows_ocr.py
--output build/windows-ocr`. This downloads direct MSYS2 ucrt64 package archives,
checks the pinned Tesseract 5.5.3-1 checksum published on its primary package
page, resolves dependencies from checksummed repository metadata, and extracts
only runtime binaries and notices. The decoder is the pinned `zstandard==0.25.0`
build dependency in the dedicated virtual environment. No package installation
script, setup executable or uninstaller is executed. Component manifests record
every package version, source recipe, license, download size and archive SHA256.

```sh
python scripts/build_linux_ocr.py --output build/linux-ocr
PLAYWRIGHT_BROWSERS_PATH="$PWD/build/browser-runtime/linux" python -m playwright install chromium
python scripts/build_browser_runtime.py --platform linux --original build/browser-runtime/linux --output build/unbranded-browser-runtime/linux
python scripts/build_release.py --ocr-dir build/linux-ocr/runtime --browsers-dir build/unbranded-browser-runtime/linux/runtime
```

The Linux OCR builder downloads authenticated Ubuntu package indexes into an
isolated temporary metadata/cache directory, downloads `.deb` files into its
own build directory, and extracts runtime files with `dpkg-deb -x`. It never
installs packages or changes system APT configuration. Each extracted package
is recorded with its version, archive size, SHA256 and copyright notice.

`BUILD-MANIFEST.json` records each bundled file's size and SHA256.
`THIRD-PARTY.json` identifies Python runtime distributions and their included
notices. OCR and browser components retain their upstream notices. For GPL/LGPL
dependencies, obtain corresponding source from the recorded upstream sources
and retain any applicable linking exceptions and notices when redistributing.
Use the complete corresponding source release alongside the application binary.

The full browser comes from fixed official unbranded Chromium snapshot archives,
with exact archive size/SHA256, publisher checksum and source commit checked by
`build_browser_runtime.py`. Chrome for Testing full binaries, Widevine DRM,
installation/elevation helpers and unused separate video helpers are excluded.
The independently licensed Chromium Headless Shell retains its complete BSD
and third-party notices. Speech/PyAV codec libraries retain their own notices
and corresponding sources.

Upstream component sources:

- [Inno Setup](https://github.com/jrsoftware/issrc), compiler license in its distribution.
- [Tesseract](https://github.com/tesseract-ocr/tesseract), Apache-2.0.
- [MSYS2 portable Tesseract package](https://packages.msys2.org/packages/mingw-w64-ucrt-x86_64-tesseract-ocr),
  with published archive checksum and complete dependency/license/source records.
- [Leptonica](https://github.com/DanBloomberg/leptonica), BSD-2-Clause.
- [Tesseract language data](https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0), Apache-2.0.
- [Ubuntu package copyright/source records](https://packages.ubuntu.com/noble/).
- [Playwright](https://github.com/microsoft/playwright), Apache-2.0; downloaded
  Chromium binaries include their separate third-party license notices.
- [Chromium source and license](https://chromium.googlesource.com/chromium/src/).

# Verification

```sh
python scripts/smoke_release.py dist/linux/StudyCenter-Public/StudyCenter-Public --output reports-local/linux-package
python scripts/check_public.py --artifact dist/linux/StudyCenter-Public
```

Use the equivalent `.exe` path for Windows. The smoke script creates only
synthetic dedicated data directories, starts the frozen executable, tests real
loopback HTTP, preview/UUID receipts/undo, CSRF rejection, local assets, an image
PDF through bundled OCR, byte-identical original access, backup download, and
cooperative shutdown. It does not authenticate any account or download models.
Inspect the frozen Python archive's filenames and constants as well as extracted
files before release. Never treat host build paths as broadly exempted secrets.
`scripts/package_audit.py` decompresses frozen Python code and reports raw,
exactly reviewed upstream constants, and unresolved matches separately. Reviewed
constants require the same module, code location, rule and SHA256.

Inspect Windows installer contents without running setup using
`scripts/package_extract_installer.py --unpacker <portable-innounp.exe>
--output <fresh-directory> <setup.exe>`. The pinned upstream Inno Unpacker
2.71.1 ZIP and executable hashes are recorded by the extractor. It verifies every
extracted application file against the build manifest; scan that extracted tree
and its decoded Python archive before publication.

Install/update/uninstall checks and desktop interaction are separate OS checks.
A successful headless smoke does not claim those checks passed. Windows setup
uses its own application ID, offers optional desktop/startup shortcuts, and
retains user data and credentials on uninstall. Linux `start.sh --stop` requests
cooperative shutdown; `install-desktop.sh` adds a desktop entry only when invoked.
The optional GitHub workflow tests/builds without cache or artifact uploads;
release publication is a separate, explicitly authorized operation.
The installer lifecycle probe refuses developer and self-hosted machines. It
runs only in an ephemeral GitHub-hosted Windows VM, uses a preinstalled Inno
compiler, and verifies selected folders with spaces/non-ASCII characters,
install/update/uninstall, preserved data and removed own optional shortcuts.
