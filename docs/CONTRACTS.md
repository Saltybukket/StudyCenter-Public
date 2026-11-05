# Shared contracts

These contracts describe the current beta.4 implementation, not a requirement
for a separate product architecture. The authoritative target is direct reuse
of the private StudyCenter product source with identical UI and behavior, as
specified in AGENTS.md and UI-PARITY.md. Adapt or replace these contracts where
needed for that integration, preserving existing user data, source evidence,
transaction integrity and installation isolation. They must not justify an
independent reimplementation or reduced functionality.

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
  Source admission rejects a claimed year contradicted by the quoted date; an
  explicitly historical semester is isolated only when the bound course has an
  unambiguous trusted semester. Older backups are migrated before replacement.
- `services.actions.ActionService(db)`: `preview(actions)`,
  `execute(actions, request_id, expected_versions=None)`, `undo(request_id, undo_id)`.
  Receipts are JSON dictionaries; retry same UUID returns same receipt.
  Personal reminder exceptions use the same versioned preview/execute/undo path.
  Source facts remain immutable under personal changes and comparison decisions.
  A personal task may explicitly reference a current source item in the same
  course. Its own start remains unchanged; a closing date is projected only for
  matching identifiers and nonconflicting, current source evidence. The source
  version participates in the creation preview's conflict check.
- `services.class_completion.ClassCompletionService(db)`: local, conservative
  completion of eligible past course sessions after their Vienna day ends. The
  ledger distinguishes automatic changes from subsequent manual status choices;
  version increments invalidate stale previews. No source fetch is performed.
- `services.calendar_export.export_calendar(items, instance_id)`: local ICS
  export with retained time precision, inclusive all-day ranges, explicit Vienna
  timezone and no fabricated end; conflicted or unclear source dates are omitted.
- `services.quests.QuestService`: explicit model-generated drafts from a personal
  goal and/or a selected item and documents. Only selected/current evidence and
  same-course PDFs already stored locally may enter the request; no crawl.
  Document pages and original hashes are checked again before draft activation.
  Mutations use version checks, UUID receipts and conflict-safe undo. Checklist
  steps have no scheduled day or daily load. Older backups get the additive tables
  before replacement; retained sources and item statuses are not mutated.
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
