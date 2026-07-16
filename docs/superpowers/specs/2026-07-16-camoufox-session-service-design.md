# Camoufox Session Service Design

## Goal

Build an independent Python service that uses Camoufox for reCAPTCHA v2 checkbox/audio, embedded Turnstile widgets, full-page challenge handling, and reusable browser sessions. The historical `turnstile-token-service` remains unchanged and no legacy API compatibility is provided.

## Scope

The first release includes:

- reCAPTCHA v2 checkbox with the existing audio-challenge path.
- Turnstile minimal-widget loading and real-page widget loading.
- Full-page challenge navigation with explicit outcome states.
- Short-lived and persistent browser sessions.
- Cookies, User-Agent, proxy identity, locale, final URL, and HTML export.
- Worker deadlines, process replacement, bounded queues, recycling, health checks, and metrics.

The first release excludes reCAPTCHA v3, reCAPTCHA Enterprise, automatic sitekey discovery, arbitrary JavaScript execution, and compatibility with the old `mode` API.

## Design Choice

Three approaches were considered:

1. Continue refactoring the mixed Node/Python service. This minimizes migration work but retains its dependency and lifecycle problems.
2. Fork FlareSolverr. This provides a mature session protocol but brings Selenium/Chromium choices that conflict with the Camoufox direction.
3. Create a small standalone Camoufox service and migrate only proven behavior. This is selected because it keeps the browser engine, API, and worker lifecycle coherent without inheriting the old API.

## Architecture

The service uses one FastAPI process and a supervised pool of Camoufox worker processes. Each worker owns one browser process. One-shot jobs use a fresh browser context and close it after completion; persistent sessions retain their context until explicit deletion or expiry.

The application is organized by responsibility, not by framework layer:

```text
camoufox-session-service/
├── pyproject.toml
├── Dockerfile
├── README.md
├── src/camoufox_service/
│   ├── app.py
│   ├── config.py
│   ├── models.py
│   ├── supervisor.py
│   ├── worker.py
│   ├── browser.py
│   ├── sessions.py
│   ├── recaptcha.py
│   ├── turnstile.py
│   └── challenge.py
└── tests/
```

Files remain focused: HTTP wiring stays in `app.py`, process lifecycle in `supervisor.py`, browser/context creation in `browser.py`, and each challenge family in one module. No repository/service/factory abstractions are introduced unless a second implementation makes them necessary.

## API

The public API is capability-based:

- `POST /v1/recaptcha/v2/solve`
- `POST /v1/turnstile/solve`
- `POST /v1/challenge/solve`
- `POST /v1/sessions`
- `POST /v1/sessions/{session_id}/request`
- `GET /v1/sessions`
- `DELETE /v1/sessions/{session_id}`
- `GET /health/live`
- `GET /health/ready`
- `GET /metrics`

Turnstile requests select `minimal` or `page` strategy. Minimal strategy intercepts the top-level target document while preserving the target origin and fulfills it with a small explicit-rendering widget page. Optional supported widget inputs are `action`, `cData`, `appearance`, `execution`, and `language`; arbitrary JavaScript is rejected.

## Result Contract

All browser tasks return a common envelope:

```json
{
  "status": "solved",
  "token": "value-or-null",
  "sessionId": "value-or-null",
  "finalUrl": "https://example.test/",
  "httpStatus": 200,
  "cookies": [],
  "userAgent": "Mozilla/5.0 ...",
  "html": null,
  "elapsedMs": 1200,
  "error": null
}
```

Challenge outcomes are `solved`, `no_challenge`, `challenge_present`, `interactive_required`, `timeout`, `browser_crashed`, and `failed`. A challenge that remains visible is a business outcome, not a successful bypass and not automatically an internal server error.

## Session Identity

A reusable session binds its browser context to its User-Agent, proxy, locale, timezone, and cookies. The service does not return a token as reusable state. Turnstile tokens are treated as single-use values. A caller that exports cookies for HTTP requests must also use the returned User-Agent and the same proxy identity.

## Worker Lifecycle

The supervisor owns worker processes and enforces:

- A hard deadline for each job.
- Termination of the entire worker process tree after timeout or browser failure.
- Failure of pending work assigned to a dead worker.
- Automatic worker replacement.
- A bounded input queue and configured concurrency.
- Recycling after a configured job count, lifetime, or memory threshold.
- Separate liveness and readiness checks.

Metrics observe lifecycle behavior but do not replace lifecycle enforcement.

## Testing

Tests are split into three groups:

1. Unit tests cover request validation, templates, challenge detection, session expiry, and worker state without launching a browser.
2. Deterministic browser integration tests use Cloudflare's official dummy Turnstile keys to verify script loading, widget rendering, callbacks, result parsing, and cleanup. These tests do not claim real-site challenge success.
3. Live acceptance tests run only when explicitly enabled and use authorized target sites to record success rate, latency, memory, and failure state. Secrets, tokens, audio URLs, and proxy credentials are redacted from logs.

reCAPTCHA live tests are opt-in because Google does not provide an equivalent deterministic production challenge. Existing verified reCAPTCHA behavior is migrated with regression tests around its HTML template, frame handling, result envelope, and session export.

## Deployment and Project Independence

The project has its own Python package metadata, dependency lock strategy, Docker image, compose example, CI workflow, license, README, environment example, and tests. It does not import files or dependencies from `turnstile-token-service` or `spider2`. Existing consumers continue using the historical service until they are migrated intentionally.

## Success Criteria

- The package installs and tests from the repository root.
- All deterministic tests pass without access to real protected sites.
- A crashed or timed-out browser worker is replaced and the next request can run.
- The existing authorized reCAPTCHA v2 flow can return token, cookies, and User-Agent.
- The Camoufox minimal Turnstile flow can render the official test widget and return its dummy token.
- Challenge requests return explicit outcome states and session data.
- No Node.js, Puppeteer, Selenium, or cross-project imports remain in the new repository.
