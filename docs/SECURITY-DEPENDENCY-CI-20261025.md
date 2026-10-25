# Dependency candidate: native CI follow-up

Candidate `70113528cd184dc3ee9c23a06fb77cf691122885` was checked by
[PR run 40199758753](https://github.com/Saltybukket/StudyCenter-Public/actions/runs/40199758753).
Windows passed tests, build, synthetic package smoke, disposable-runner installer
lifecycle and unpacked content inspection. Linux reported 528 passed / 21 skipped;
build and core smoke passed. Its separate bundled-browser probe failed because
the runner refused the Chromium sandbox. The existing optional-probe policy is
unchanged; no Linux browser acceptance is claimed.

Linux content inspection failed on two paths for the same GIO binary. The portable
OCR builder resolves the current signed Ubuntu index; GIO advanced from
`2.80.0-6ubuntu3.9` to `2.80.0-6ubuntu3.10`. An independent download of the
[official package](https://archive.ubuntu.com/ubuntu/pool/main/g/glib2.0/libglib2.0-0t64_2.80.0-6ubuntu3.10_amd64.deb)
has archive SHA256
`ff1b650f655b36dfe202fdbae6bb02b363c7257b6dd23e4f95ab61f716190df3`.
Its `usr/lib/x86_64-linux-gnu/libgio-2.0.so.0.8000.0` member has SHA256
`373801faa40cd4eb46a40ce02bd0945b4905160a59894b600636e0fadfb78f2b`,
identical to both failed CI paths. Across the scanner's raw and two UTF-16
variants there is one credential-rule match: the existing public encrypted-PEM
format delimiter, followed by NUL and parser error messages, without a key body.

The existing whole-file review policy now records this additional exact version
for those two paths and that rule only. The prior review is retained. Scanner
rules, workflow, sandbox settings, dependencies and assertions are unchanged.
Changed bytes, another path or another rule still fail. Focused content-gate tests
and an actual public-binary positive/mutated-byte negative check cover this delta.
The updated candidate requires its own exact-head CI result; this document does
not claim a successful future build, full Linux acceptance or a release.
