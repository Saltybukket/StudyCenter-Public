# Model providers and account connections

The app starts offline. Connecting an account, listing models, probing a provider,
and generating a response are explicit actions. Tests use synthetic transports;
passing them does not establish a real account's entitlement or successful paid
inference. No environment API keys, browser credentials, or another application's
OAuth registration are discovered or reused.

## Profiles and secrets

`ProviderProfile(provider, model, base_url, profile_id, allow_lan=False)` contains
only non-secret settings. API keys are saved through this installation's
`SecretStore` under `provider/<profile_id>`. Its credential namespace is established
by the platform. If an OS credential store is unavailable, the user must explicitly
choose session-only storage. Profiles serialize no keys or OAuth tokens.

`ProviderSettings(path, secrets)` offers `list_profiles`, `save_profile`, `select`,
`active_profile`, `set_api_key`, and a safe `status`. Changing a profile selects its
own key; no fallback to another account or provider exists.

| Profile | Protocol | Preset API base |
|---|---|---|
| `openai` | Responses | `https://api.openai.com/v1` |
| `openai-chat` | Chat Completions | `https://api.openai.com/v1` |
| `chatgpt` | Scoped OAuth, streaming Responses | Official OpenAI API only |
| `anthropic` | Native Messages | `https://api.anthropic.com/v1` |
| `gemini` | Native generateContent | `https://generativelanguage.googleapis.com/v1beta` |
| `gemini-compatible` | Official Chat Completions compatibility | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `deepseek` | Chat Completions | `https://api.deepseek.com` |
| `compatible` | Chat Completions | Explicit HTTPS API base required |
| `ollama` | Native chat/tags | Explicit own local server required |
| `lmstudio` | OpenAI compatibility | Explicit own local server; `/v1` added to root URLs |

Local servers may use a literal loopback address. A literal private LAN address
requires `allow_lan=True`; that choice sends source data to the specified server.
Link-local, unspecified and multicast addresses are rejected. Profiles reject
embedded credentials, query strings and fragments. Remote cleartext HTTP is
rejected. No server scanning or automatic LAN discovery takes place.

Protocol references: [Anthropic Messages](https://platform.claude.com/docs/en/api/messages),
[Gemini generateContent](https://ai.google.dev/api/generate-content),
[DeepSeek Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/),
[Ollama chat](https://docs.ollama.com/api/chat), and
[LM Studio compatibility](https://lmstudio.ai/docs/developer/openai-compat).

## Shared tasks and complete results

`create_provider(profile, secrets, transport=None, oauth=None)` creates the
explicit adapter. Its async `list_models`, `probe`, and `generate` methods share
`ModelRequest` and `ModelResult`. Probe only lists models. All providers accept
`extraction`, `assistant`, and `questions` through the same contract.

Input is serialized completely, including the schema. Excess input fails before
any request; it is never silently truncated. Source data is marked as untrusted
material. A supplied JSON schema is checked before sending and validated again
against the final parsed output, including formats. Local JSON-pointer schema references are expanded within strict size, depth and
node bounds. Remote or dynamic references, scope changes, cycles and dangling
pointers fail before any request, so validation cannot retrieve remote resources. The domain marks its naive Europe/Vienna source clocks with the explicit
`studycenter-local-time` format: `HH:MM`, optional seconds, and optional one to six
fractional digits after seconds. Actual clock validity is checked; offsets are
rejected, and Candidate still requires the corresponding source date. Generic
`time` schemas retain their ordinary checker, and `date-time` explicitly validates
a full RFC3339 date, clock and offset even without optional checker libraries.
Malformed, empty, refused, incomplete or truncated output raises a safe categorized
`ProviderError`.
OpenAI Responses uses provider-enforced strict JSON schema only for a known
closed, all-required subset. More general application schemas use documented JSON
object mode, their complete schema in the instructions, and full local validation;
this does not claim provider-enforced schema conformance. See
[structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
Adapters require each native protocol's completion marker. No partial result is
returned as successful, and generated suggestions do not themselves write state.

HTTP clients disable redirects and environment proxy configuration, use bounded
connect/read timeouts and response sizes, and perform no automatic paid retry.
Catalog pagination is bounded and does not silently return an unfinished catalog.
Remote response bodies and credentials are absent from surfaced error messages.

## Continue with ChatGPT

The implementation follows the documented
[open-source registration flow](https://developers.openai.com/siwc/token-sharing-open-source/sign-in).
`ChatGPTAuth(secrets, host_id)` receives this installation's persistent `urn:uuid`
host identity. `start(port=0, registration_id=None)` binds `127.0.0.1` before returning
an authorization URL and callback URI. The application opens that URL in the
system browser. `wait_for_callback()` completes the attempt; `finish_callback(url)`
exists for protocol integration. A fresh state, nonce and S256 PKCE verifier bind
each attempt to its exact callback scheme, host, port and `/auth/callback` path.

Initial registration uses `dynamic_agent_client` and the real app name. The
callback-issued client ID is used for token exchange. Returning registration
retains its own client ID and workspace binding. Before storing credentials, the
app verifies the ID-token signature against trusted OpenAI discovery/JWKS,
issuer, audience, expiry, subject and pending nonce. Reauthorization must preserve
the selected subject and client ID. A denied or invalid callback consumes the
attempt without replacing an active registration.

[Accounts and sessions](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions)
remain separate by registration ID. `accounts()` exposes safe labels and account
metadata; `select(id)` changes the selected registration. Tokens remain solely in
protected storage. Refreshes are serialized per session and rotating token sets
are replaced together. `logout(id)` attempts remote refresh-token revocation,
clears local tokens, and reports whether remote revocation was confirmed. Account
and client mappings and the host ID remain available for later sign-in.

The [granted scopes and token reference](https://developers.openai.com/siwc/token-sharing-open-source/token-reference)
determine inference permission. A valid identity token alone is insufficient.
The [account-specific models and inference flow](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference)
uses `/v1/models`, retains displayed model slugs, and calls `/v1/responses` with
`store=false` and `stream=true`. A stream succeeds only after `response.completed`;
failed, interrupted or incomplete streams fail closed. No Codex client identity or
private ChatGPT backend endpoint is used.

Live account registration, workspace policies, model access and OS keyring behavior
must be checked separately with the user's own explicitly configured installation.
