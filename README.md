# Camoufox Session Service

An independent Python service that runs Camoufox browser tasks behind a small HTTP API. It supports:

- reCAPTCHA v2 checkbox and audio challenge.
- Turnstile minimal-widget and full-page strategies.
- Full-page challenge detection and session export.
- Persistent browser contexts for follow-up requests.
- Supervised workers with hard timeouts, process-tree replacement, bounded queues, and recycling.

It does not depend on `turnstile-token-service`, Node.js, Puppeteer, Selenium, or any sibling repository.

## Requirements

- Python 3.11+
- ffmpeg and ffprobe for reCAPTCHA audio conversion
- Camoufox browser binaries

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m camoufox fetch
python -m uvicorn camoufox_service.app:app --host 0.0.0.0 --port 3000
```

On Windows, activate with `.venv\Scripts\activate`.

## API

### Turnstile minimal widget

```bash
curl -X POST http://127.0.0.1:3000/v1/turnstile/solve \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.test",
    "siteKey": "1x00000000000000000000AA",
    "strategy": "minimal"
  }'
```

Use `strategy: "page"` to load the real page and read its existing widget. `action`, `cData`, `appearance`, `execution`, and `language` are accepted as typed widget options. Caller-provided JavaScript is not accepted.

### reCAPTCHA v2 checkbox/audio

```bash
curl -X POST http://127.0.0.1:3000/v1/recaptcha/v2/solve \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://authorized.example/captcha",
    "sessionUrl": "https://authorized.example/",
    "siteKey": "site-key",
    "maxAudioAttempts": 3
  }'
```

Only reCAPTCHA v2 checkbox/audio is implemented. v3 and Enterprise are outside project scope.

### Full-page challenge

```bash
curl -X POST http://127.0.0.1:3000/v1/challenge/solve \
  -H "Content-Type: application/json" \
  -d '{"url":"https://authorized.example/","waitSeconds":30,"returnHtml":true}'
```

Possible outcomes include `solved`, `no_challenge`, `challenge_present`, `interactive_required`, `timeout`, and `failed`. The endpoint reports observed state; it does not promise that every Managed Challenge will pass.

### Persistent sessions

Create an empty context:

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"ttlSeconds":900}'
```

To reuse a solver result inside the service, pass its cookies back when creating the
session and keep the same `userAgent` and proxy identity:

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{
    "ttlSeconds": 900,
    "userAgent": "USER_AGENT_FROM_SOLVER_RESULT",
    "cookies": [{
      "name": "cf_clearance",
      "value": "COOKIE_VALUE_FROM_SOLVER_RESULT",
      "domain": ".example.test",
      "path": "/",
      "secure": true,
      "httpOnly": true
    }]
  }'
```

Use the returned `sessionId`:

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions/SESSION_ID/request \
  -H "Content-Type: application/json" \
  -d '{"method":"GET","url":"https://example.test","returnHtml":true}'
```

Delete it with `DELETE /v1/sessions/SESSION_ID`. Cookies exported for direct HTTP
reuse must stay paired with the returned User-Agent and the same proxy identity.
If a session's browser worker restarts, that session is invalidated and the request
returns HTTP 410 instead of silently using a fresh browser identity.

## Configuration

Copy `.env.example` to `.env`. Set `AUTH_TOKEN` to require `Authorization: Bearer <token>` on business and metrics endpoints. Worker count, queue size, task timeout, session TTL, recycle thresholds, and headless mode are configurable through the documented environment variables.

## Testing

```bash
python -m pytest -q
```

The default suite is deterministic and does not access protected sites. The optional browser test uses Cloudflare's official dummy sitekey:

```bash
RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser_integration.py -q
```

Dummy keys verify browser startup, document interception, widget rendering, callback capture, and cleanup. They do not measure real-site success. Live acceptance tests must be run separately against sites you own or are authorized to test.

## Docker

```bash
docker compose build
docker compose up -d
curl http://127.0.0.1:3000/health/ready
```

The image contains no Chromium or Node.js runtime. Worker subprocesses own Camoufox instances; if a browser task exceeds its hard deadline, the supervisor terminates that process tree and starts a replacement before accepting more work on that slot.
