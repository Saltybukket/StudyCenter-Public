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
No system OCR installer or uninstaller is included. Portable Chromium and its
Headless Shell are bound to the pinned Playwright driver version and include their
upstream licenses. Optional Whisper weights are downloaded only after explicit
selection from pinned source revisions and are checked against the recorded hashes.

Release source and build instructions correspond to the public release tag.
The package manifest records the SHA256 of shipped files; release checksums cover
the resulting distributions. Licensing records are provenance information, not an
invented legal or security certification.
