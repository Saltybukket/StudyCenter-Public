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
- `services.actions.ActionService(db)`: `preview(actions)`,
  `execute(actions, request_id, expected_versions=None)`, `undo(request_id, undo_id)`.
  Receipts are JSON dictionaries; retry same UUID returns same receipt.
- `providers.contracts.ModelRequest`: task (`extraction`, `assistant`, `questions`),
  instructions, source_data, schema, max_input_chars. `ModelResult`: content,
  complete, provider, model, usage. `ModelProvider`: async `list_models`, `probe`,
  `generate`. Factory uses explicit profiles and SecretStore, never environment keys.
- `collectors`: SourceCollector result distinguishes authentication, reachability,
  completion, change and gaps. BrowserManager owns only this installation's profile.
- `services.documents.DocumentService(db, paths)`: import, versioned pages,
  source text and original access. `services.sync.SyncService`: explicit manual sync.
- `web.create_app(paths, secrets=None)`: mounted routers and local assets;
  all writes require same-origin CSRF; read-only local MCP is off by default.
- Entry point: `python -m studycenter_public --data-dir PATH --port 8767
  --no-browser`; frozen executable calls the same main in-process.

The document owner and core owner coordinate their DB-facing methods before
integration. Optional imports must not break an empty offline start.
