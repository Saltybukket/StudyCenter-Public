# Third-party components

StudyCenter Public and the integrated first-party source/UI assets are distributed
under AGPL-3.0-only. The full license is shipped and reachable from the application.

PyMuPDF is used under its AGPL option, not a commercial license. Python, FastAPI,
Starlette, Pydantic, Jinja2, HTTPX, Beautiful Soup, Playwright, keyring, JSON Schema,
PyJWT, cryptography and optional Windows shell/speech libraries retain their own
upstream notices. They are collected with exact versions in `THIRD-PARTY.json`
and the package's `licenses/` directory by the native build.

Portable OCR includes Tesseract (Apache-2.0), Leptonica and required image/runtime
dependencies, with their own notices and recorded distribution sources/checksums.
No system OCR installer or uninstaller is included. Full unbranded Chromium
snapshots and Linux's Chromium Headless Shell have separate pinned archive/source
commits and complete original notices. Windows uses the full unbranded Chromium
for both visible and headless work and excludes the Windows Headless Shell.
The shared source companion retains the unchanged sources/notices for the latter's
earlier held test candidates. The bundled Playwright driver is version-pinned
independently. Windows Dawn's DirectX compiler retains its original NCSA/LLVM
notices. The WebView2 SDK files retain their Microsoft BSD-3-Clause license and
NOTICE, separate from an installed Evergreen runtime. Optional Whisper weights are downloaded only after explicit
selection from pinned source revisions and are checked against the recorded hashes.

Release source and build instructions correspond to the public release tag.
The package manifest records the SHA256 of shipped files; release checksums cover
the resulting distributions. Licensing records are provenance information, not an
invented legal or security certification.

This beta omits Microsoft CRT/UCRT, API-set forwarding DLLs and the Windows SDK
D3DCompiler payload. Existing compatible x64 Windows system components are checked
as prerequisites. Two unsigned NumPy consumers have their DLL import-name bytes
changed to the canonical system name, with exact original/patched hashes and the
reproducible public recipe recorded in `SYSTEM-RUNTIME-RECIPE.json`. No Microsoft
system-runtime binary, Redistributable installer or local renamed Microsoft DLL is shipped by
that recipe. A future one-click prerequisite flow requires a separately verified
official Microsoft distribution path; no Visual Studio entitlement is inferred.
