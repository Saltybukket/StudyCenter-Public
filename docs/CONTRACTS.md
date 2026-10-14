# Shared contracts

Integration owns this file and project configuration. Module owners may create
their own tests but do not change other owners' files or commit/push.

- `platform.paths.AppPaths.create(data_dir=None)`: `data`, `cache`, `config`,
  `logs`, `profiles`, `documents`, `database`, `instance_id`, `host_id`.
- `platform.secrets.SecretStore(instance_id, session_only=False)`: `get(service)`,
  `set(service, value)`, `delete(service)`, `persistent` and `status`.
  Explicit session mode when no secure keyring exists. No plaintext fallback.
- `storage.db.Database(path)`: committed evidence algorithm, `connection()` and
  `items()`/`item(id)` projected personal state; migrations and action receipts.
  `upsert_course(source_id, external_id, title, metadata)` owns course identity.
  Document ingestion binds a trusted selected course; a model-supplied course ID
  never establishes provenance. Unbound imports get distinct document families.
- `services.actions.ActionService(db)`: `preview(actions)`,
  `execute(actions, request_id, expected_versions=None)`, `undo(request_id, undo_id)`.
  Receipts are JSON dictionaries; retry same UUID returns same receipt.
  Personal reminder exceptions use the same versioned preview/execute/undo path.
  Source facts remain immutable under personal changes and comparison decisions.
- `providers.contracts.ModelRequest`: task (`extraction`, `assistant`, `questions`),
  instructions, source_data, schema, max_input_chars. `ModelResult`: content,
  complete, provider, model, usage. `ModelProvider`: async `list_models`, `probe`,
  `generate`. Factory uses explicit profiles and SecretStore, never environment keys.
- `collectors`: SourceCollector result distinguishes authentication, reachability,
  completion, change and gaps. BrowserManager owns only this installation's profile.
- `services.documents.DocumentService(db, paths)`: import, versioned pages,
  source text and original access. `services.sync.SyncService`: explicit manual sync.
  `source_text` serves retained reader evidence; `analysis_text` enforces current
  analysis exclusions. OCR invokes only the absolute owned component executable.
- `web.create_app(paths, secrets=None)`: mounted routers and local assets;
  all writes require same-origin CSRF; read-only local MCP is off by default.
  MCP permissions and tokens are independent from UI credentials; responses have
  bounded page and text continuation. Secrets have no reader endpoint.
- Entry point: `python -m studycenter_public --data-dir PATH --port 8767
  --no-browser`; frozen executable calls the same main in-process.
  `--quit` requests only this data directory's cooperative shutdown;
  `--background` suppresses foreground opening for an explicitly selected autostart.
  Portable OCR/browser components reside inside the frozen installation; development
  uses its own component cache and never an inherited general browser/OCR location.

The document owner and core owner coordinate their DB-facing methods before
integration. Optional imports must not break an empty offline start.
