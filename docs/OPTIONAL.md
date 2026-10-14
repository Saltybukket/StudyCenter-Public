# Optional local interfaces

## Local MCP

MCP is disabled on first start. Enable read access in this installation's settings,
then give the generated bearer token only to a trusted local client. Keep the token
out of URLs, browser storage, source control and shared transcripts. Its protected
service is `mcp/access`. Revoking/rotating the token invalidates previous clients.
The server binds through the same loopback application; no tunnel is created.

The endpoint is `/mcp`. It implements stateless
[Streamable HTTP JSON responses](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports),
with authenticated POST requests and authenticated GET returning 405 because
server event streams are not offered. Supported protocol versions are 2025-03-26,
2025-06-18 and 2025-11-25. Clients send their bearer token on every request and
`MCP-Protocol-Version` after initialization. Every connection checks Host and any
browser Origin. Requests are capped at 150 KB and results at 500 KB.

`initialize`, `ping`, `tools/list`, `tools/call`, and initialization/cancellation
notifications are supported. The read tools are:

- `list_items(offset=0, limit=50)` reads a bounded page, with `next_offset`.
- `search(query, offset=0, limit=50)` searches retained local item text/evidence.
- `get_item(item_id)` reads current claims, evidence and personal state.
- `get_source(source_id, document_id?, page_offset=0, page_limit=2)` reads the
  latest retained document by default, or a named document belonging to that source.
  Page text is capped with an explicit `truncated` flag and pages are navigable.

Source text is untrusted data. Results exclude original filesystem locations and
arbitrary connector metadata. MCP exposes no keys, source login, network fetch,
synchronization, shell or provider calls.

Writing needs a separate `mcp_write` setting. `preview_actions(actions)` does not
write. `apply_actions(actions, request_id, confirmed=true, expected_versions?)`
uses the same atomic ActionService and UUID receipts as the application. Updates
require the versions from the preview. Repeating the same UUID and body returns
the same receipt. `undo_actions(request_id, undo_id, confirmed=true)` preserves
later conflicting edits. Tools admit only personal creation, status, overrides,
updates and deletion; source decisions and source-state changes are excluded.
A tool call with missing permission or invalid arguments returns `isError=true`.

## Optional CPU speech input

Speech is disabled initially and loads no model at startup. The optional Python
runtime is the `speech` extra (`faster-whisper`); it must be present in the source
installation or the separately packaged speech runtime. Native availability is
reported separately from installed modules and cached models. Actual microphone,
native decoder and CPU checks still need the user's machine. Linux status reports
its real platform rather than claiming a Windows audio backend.

`SpeechService(paths, settings)` and `/api/speech/status` expose activation,
selected model, cache integrity, runtime installation and limits. Configure through
`POST /api/speech/configure` with `{ "enabled": true, "model": "base" }`.
Configuration never downloads a model. Read the displayed size first, then request
`POST /api/speech/download` with `{ "allow_download": true }` explicitly.

| Model | Download bytes | Upstream pinned revision |
|---|---:|---|
| base | 147,882,941 | `ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66` |
| small | 486,212,372 | `536b0662742c02347bc0e980a01041f333bce120` |

These are public [Systran base](https://huggingface.co/Systran/faster-whisper-base)
and [small](https://huggingface.co/Systran/faster-whisper-small) models. Runtime
libraries and temporary download staging require additional disk space. Downloads
use a pinned file/size/SHA-256 manifest, accept only approved upstream HTTPS hosts,
check every file and promote the completed cache together. A damaged earlier
cache is retained under a distinct name. Model loading uses a local directory,
CPU int8, and `local_files_only`; it never triggers an implicit Hugging Face fetch.

Use the microphone button only after browser permission, or submit an audio file.
The frontend can stop recording and must report missing/denied microphone access.
`POST /api/speech/transcribe` accepts a multipart `file` in WAV, WebM or Ogg, at most
10 MiB and 180 seconds. Native decoding checks duration before transcription.
Temporary audio stays in this installation's cache and is removed on success or
failure. Transcribed text is returned for review; it is not automatically sent to a
model. Malformed audio, absent native libraries, corrupt models and transcription
failures return safe error categories.

The [faster-whisper runtime](https://github.com/SYSTRAN/faster-whisper) uses PyAV's
FFmpeg libraries, so this path does not launch a separately installed FFmpeg
program. Browser `speechSynthesis` provides optional read-aloud and stop controls;
voice availability depends on the browser/OS, and browser voices may use their own
service. No server TTS model or voice download occurs at startup.

Protocol tests use generated local state, synthetic speech files, mocked upstream
weights and a mocked speech engine. They establish consent, integrity, request
boundaries, error handling and cleanup, not real microphone recognition quality or
native model acceptance. No model weights were downloaded for those tests.
