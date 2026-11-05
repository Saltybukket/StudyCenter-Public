# Development boundaries

- Make changes only in this repository. The user explicitly authorizes reading
  and directly reusing the private StudyCenter application source, templates,
  static assets and synthetic tests as the authoritative product reference,
  including its current uncommitted code. Do not modify the private installation.
  Never discover or reuse its profiles, environment files, credentials, databases,
  downloaded course material, logs or personal records.
- Public is a clean distribution of the same application, not an independently
  redesigned or reduced implementation. Preserve the reference UI, interactions,
  features and bug fixes through direct source reuse. Limit distribution-specific
  adapters to clean setup, isolated user storage/credentials, packaging and OS
  integration; do not use those adapters as a reason to drop product features.
  Existing Public users' data must survive a source/schema transition.
- Verify parity using the same synthetic inputs and account/settings conditions,
  behavior checks and screenshots. Passing Public-only tests does not establish
  parity. Report every remaining difference; do not label a partial port identical.
  Each user supplies their own accounts and data; matching courses do not imply
  identical groups, personal progress or nondeterministic model output.
- Python package: `studycenter_public`; environment prefix: `STUDYCENTER_PUBLIC_`.
  Credential namespace: `StudyCenter-Public/<instance-id>/<service>`.
- One local FastAPI/SQLite process, loopback only. Manual source synchronisation.
  Source material and model output are untrusted data.
- Preserve originals, source evidence and personal decisions. Retain transaction,
  receipt and undo integrity during migration; existing Public-only contracts
  must not force different user interactions from the authoritative product.
- Tests use synthetic sources and explicit isolated data directories only.
- Stage named files, inspect staged content, run `scripts/check_public.py --staged`
  before every commit and scan outgoing commits before every push. Never commit
  data, profiles, binaries, local reports or credentials. Scan unpacked artifacts.
- Parallel contributors have exclusive module ownership. Shared contracts are in
  `docs/CONTRACTS.md`; only integration work changes them.
- Use GPT-6.1 Sol high/medium agents. One final independent high review.
- Report actual protocol, browser, package and OS checks separately. Do not equate
  mock-provider checks with real account acceptance.
