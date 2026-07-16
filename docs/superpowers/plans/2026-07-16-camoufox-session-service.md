# Camoufox Session Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver an independent, installable Python/Camoufox service for reCAPTCHA v2 checkbox/audio, Turnstile widgets, full-page challenge outcomes, and reusable browser sessions.

**Architecture:** A FastAPI process sends typed JSON jobs to a supervised pool of worker subprocesses. Each worker owns one Camoufox browser; one-shot calls use disposable contexts while named sessions retain contexts inside their owning worker. Challenge modules contain browser behavior and share only the small helpers in `browser.py`.

**Tech Stack:** Python 3.11, FastAPI, Pydantic 2, Camoufox/Playwright sync API, psutil, pytest, Docker.

## Global Constraints

- The historical `turnstile-token-service` stays unchanged.
- The new API does not accept legacy `mode` values.
- reCAPTCHA support is limited to v2 checkbox and its audio challenge.
- No Node.js, Puppeteer, Selenium, cross-project imports, arbitrary JavaScript input, or speculative abstraction layers.
- Turnstile dummy keys prove deterministic browser integration only; live tests are opt-in.
- Tokens, audio payload URLs, proxy credentials, and authentication values must not be logged.

---

### Task 1: Package, configuration, and API contracts

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example`
- Create: `src/camoufox_service/__init__.py`
- Create: `src/camoufox_service/config.py`
- Create: `src/camoufox_service/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Produces: `Settings.from_env()`, `ProxyConfig`, `BrowserOptions`, `RecaptchaV2Request`, `TurnstileRequest`, `ChallengeRequest`, `SessionCreateRequest`, `SessionRequest`, `TaskResult`, and `ErrorInfo`.
- Request models normalize a string or object proxy to a single `ProxyConfig` and reject unknown fields.

- [ ] **Step 1: Write failing contract tests**

```python
def test_turnstile_rejects_unknown_strategy():
    with pytest.raises(ValidationError):
        TurnstileRequest(url="https://example.test", siteKey="key", strategy="legacy")

def test_task_result_has_stable_envelope():
    result = TaskResult(status="solved", token="abc", elapsedMs=12)
    assert result.model_dump()["cookies"] == []
    assert result.model_dump()["error"] is None
```

- [ ] **Step 2: Confirm tests fail before package creation**

Run: `python -m pytest tests/test_models.py -q`
Expected: collection fails because `camoufox_service.models` does not exist.

- [ ] **Step 3: Add the minimal package and strict Pydantic models**

```python
class TurnstileRequest(BrowserOptions):
    url: HttpUrl
    siteKey: str | None = None
    strategy: Literal["minimal", "page"] = "minimal"
    action: str | None = None
    cData: str | None = None

class TaskResult(BaseModel):
    status: Outcome
    token: str | None = None
    sessionId: str | None = None
    finalUrl: str | None = None
    httpStatus: int | None = None
    cookies: list[Cookie] = Field(default_factory=list)
    userAgent: str | None = None
    html: str | None = None
    elapsedMs: int
    error: ErrorInfo | None = None
```

- [ ] **Step 4: Run contract tests**

Run: `python -m pytest tests/test_models.py -q`
Expected: all model and environment tests pass.

- [ ] **Step 5: Commit the package contracts**

```bash
git add pyproject.toml .gitignore .env.example src tests/test_models.py
git commit -m "feat: define service contracts"
```

### Task 2: Browser helpers, Turnstile, and challenge outcomes

**Files:**
- Create: `src/camoufox_service/browser.py`
- Create: `src/camoufox_service/turnstile.py`
- Create: `src/camoufox_service/challenge.py`
- Create: `tests/test_turnstile.py`
- Create: `tests/test_challenge.py`

**Interfaces:**
- Produces: `context_options(options) -> dict`, `cookies_from_context(context) -> list[Cookie]`, `build_turnstile_html(request) -> str`, `solve_turnstile(browser, request) -> TaskResult`, `detect_challenge(page) -> ChallengeEvidence`, and `solve_challenge(browser, request) -> TaskResult`.
- `solve_turnstile` supports `minimal` document fulfillment and `page` loading without accepting caller-provided JavaScript.

- [ ] **Step 1: Write failing pure-function tests**

```python
def test_turnstile_template_escapes_values():
    html = build_turnstile_html(TurnstileRequest(
        url="https://example.test", siteKey='key"</script>', strategy="minimal"
    ))
    assert 'key"</script>' not in html
    assert "turnstile.render" in html

def test_detects_cloudflare_interstitial(fake_page):
    fake_page.title.return_value = "Just a moment..."
    fake_page.url = "https://example.test/"
    fake_page.content.return_value = '<form id="challenge-form"></form>'
    assert detect_challenge(fake_page).detected is True
```

- [ ] **Step 2: Confirm the focused tests fail**

Run: `python -m pytest tests/test_turnstile.py tests/test_challenge.py -q`
Expected: imports fail for the missing modules.

- [ ] **Step 3: Implement templates, context helpers, and explicit outcomes**

```python
def build_turnstile_html(request: TurnstileRequest) -> str:
    config = {"sitekey": request.siteKey, "action": request.action, "cData": request.cData}
    safe_config = json.dumps({key: value for key, value in config.items() if value is not None})
    return TURNSTILE_TEMPLATE.replace("__CONFIG__", safe_config.replace("</", "<\\/"))

def detect_challenge(page) -> ChallengeEvidence:
    title = (page.title() or "").lower()
    body = (page.content() or "").lower()
    detected = "just a moment" in title or "challenge-form" in body or "cf-chl-" in body
    return ChallengeEvidence(detected=detected, vendor="cloudflare" if detected else None)
```

- [ ] **Step 4: Run pure tests and optional dummy-key browser test**

Run: `python -m pytest tests/test_turnstile.py tests/test_challenge.py -q`
Expected: pure tests pass; browser test is skipped unless `RUN_BROWSER_TESTS=1`.

- [ ] **Step 5: Commit browser challenge behavior**

```bash
git add src/camoufox_service/browser.py src/camoufox_service/turnstile.py src/camoufox_service/challenge.py tests
git commit -m "feat: add turnstile and challenge tasks"
```

### Task 3: Worker protocol, supervisor, sessions, and FastAPI

**Files:**
- Create: `src/camoufox_service/worker.py`
- Create: `src/camoufox_service/supervisor.py`
- Create: `src/camoufox_service/sessions.py`
- Create: `src/camoufox_service/app.py`
- Create: `tests/test_supervisor.py`
- Create: `tests/test_sessions.py`
- Create: `tests/test_api.py`

**Interfaces:**
- Produces: `WorkerSupervisor.start()`, `WorkerSupervisor.request(kind, payload, timeout)`, `WorkerSupervisor.stop()`, `SessionRegistry.create()`, `SessionRegistry.get()`, `SessionRegistry.delete()`, `SessionRegistry.expire()`, and `create_app(supervisor=None)`.
- The worker JSON-lines protocol uses `{id, kind, payload}` requests and `{id, result}` or `{id, error}` responses.

- [ ] **Step 1: Write failing lifecycle tests with a fake worker command**

```python
@pytest.mark.asyncio
async def test_timeout_replaces_worker(fake_worker_command):
    supervisor = WorkerSupervisor([fake_worker_command], task_timeout=0.05)
    first_pid = await supervisor.start()
    with pytest.raises(WorkerTimeout):
        await supervisor.request("sleep", {"seconds": 1})
    assert supervisor.pid != first_pid

def test_session_expiry_uses_monotonic_clock():
    registry = SessionRegistry(ttl_seconds=10, clock=lambda: 20)
    registry.create("s1", worker_id=0, created_at=0)
    assert registry.expire() == ["s1"]
```

- [ ] **Step 2: Confirm lifecycle and API tests fail**

Run: `python -m pytest tests/test_supervisor.py tests/test_sessions.py tests/test_api.py -q`
Expected: missing worker, registry, and app modules.

- [ ] **Step 3: Implement the smallest supervised process and session registry**

```python
async def request(self, kind: str, payload: dict, timeout: float | None = None) -> dict:
    worker = await self._pool.acquire()
    try:
        return await asyncio.wait_for(worker.request(kind, payload), timeout or self.task_timeout)
    except (TimeoutError, WorkerExited) as exc:
        await self._replace(worker)
        raise WorkerTimeout(str(exc)) from exc
    finally:
        self._pool.release_if_alive(worker)
```

The replacement path terminates the subprocess tree with psutil, fails its pending futures, and starts a fresh process before it becomes available.

- [ ] **Step 4: Add FastAPI routes that only validate and dispatch**

```python
@router.post("/v1/turnstile/solve", response_model=TaskResult)
async def turnstile_solve(request: TurnstileRequest):
    return await supervisor.request("turnstile.solve", request.model_dump(mode="json"))
```

Map validation to 422, queue saturation to 429, task timeout to 504, and worker failure to 503. Challenge business outcomes remain HTTP 200 with their explicit `status`.

- [ ] **Step 5: Run lifecycle and API tests**

Run: `python -m pytest tests/test_supervisor.py tests/test_sessions.py tests/test_api.py -q`
Expected: timeout replacement, bounded queue, session expiry, health, and route tests pass.

- [ ] **Step 6: Commit service orchestration**

```bash
git add src/camoufox_service/worker.py src/camoufox_service/supervisor.py src/camoufox_service/sessions.py src/camoufox_service/app.py tests
git commit -m "feat: supervise workers and sessions"
```

### Task 4: Migrate the verified reCAPTCHA v2 audio path

**Files:**
- Create: `src/camoufox_service/recaptcha.py`
- Create: `src/camoufox_service/recaptcha_audio.py`
- Create: `tests/test_recaptcha.py`
- Modify: `src/camoufox_service/worker.py`
- Modify: `src/camoufox_service/app.py`

**Interfaces:**
- Produces: `build_recaptcha_html(request) -> str`, `RecaptchaV2Solver.solve() -> TaskResult`, and the `recaptcha.v2.solve` worker job.
- Migrates the working frame/audio behavior from the historical project, removes service-specific logging and cross-project assumptions, and keeps all token/cookie/UA output in `TaskResult`.

- [ ] **Step 1: Write failing template, redaction, and result tests**

```python
def test_recaptcha_template_escapes_site_key():
    html = build_recaptcha_html(RecaptchaV2Request(
        url="https://example.test", siteKey='bad"<script>', query="AAA"
    ))
    assert 'bad"<script>' not in html
    assert "g-recaptcha-response" in html

def test_audio_url_is_redacted():
    assert redact_url("https://google.com/recaptcha/api2/payload?p=secret") == \
        "https://google.com/recaptcha/api2/payload"
```

- [ ] **Step 2: Confirm reCAPTCHA tests fail**

Run: `python -m pytest tests/test_recaptcha.py -q`
Expected: missing `camoufox_service.recaptcha`.

- [ ] **Step 3: Migrate only the proven flow**

Keep the existing sequence: render checkbox, open audio challenge when required, download audio through the browser-backed session, convert with ffmpeg/pydub, transcribe with SpeechRecognition, submit, and read `g-recaptcha-response`. Limit attempts using the request value and return `interactive_required`, `timeout`, or `failed` instead of unstructured strings.

- [ ] **Step 4: Wire worker and API route**

```python
@router.post("/v1/recaptcha/v2/solve", response_model=TaskResult)
async def recaptcha_v2_solve(request: RecaptchaV2Request):
    return await supervisor.request("recaptcha.v2.solve", request.model_dump(mode="json"))
```

- [ ] **Step 5: Run reCAPTCHA and full unit suites**

Run: `python -m pytest -q`
Expected: all unit tests pass; live reCAPTCHA tests remain skipped unless explicitly enabled.

- [ ] **Step 6: Commit reCAPTCHA capability**

```bash
git add src/camoufox_service/recaptcha.py src/camoufox_service/recaptcha_audio.py src/camoufox_service/worker.py src/camoufox_service/app.py tests/test_recaptcha.py
git commit -m "feat: add recaptcha v2 audio solver"
```

### Task 5: Docker, CI, documentation, and Linux verification

**Files:**
- Create: `Dockerfile`
- Create: `compose.yaml`
- Create: `README.md`
- Create: `LICENSE`
- Create: `.dockerignore`
- Create: `.github/workflows/ci.yml`
- Create: `tests/test_browser_integration.py`

**Interfaces:**
- Produces: an image that starts `uvicorn camoufox_service.app:app`, a compose example with bounded resources, and documented request/response examples for every endpoint.

- [ ] **Step 1: Add an opt-in official Turnstile dummy-key integration test**

```python
@pytest.mark.skipif(os.getenv("RUN_BROWSER_TESTS") != "1", reason="browser integration disabled")
def test_official_turnstile_dummy_key(worker_browser):
    result = solve_turnstile(worker_browser, TurnstileRequest(
        url="https://example.test", siteKey="1x00000000000000000000AA", strategy="minimal"
    ))
    assert result.status == "solved"
    assert result.token
```

- [ ] **Step 2: Add packaging, image, CI, and operator documentation**

The Docker image installs Python, ffmpeg, Xvfb, Camoufox browser binaries, and the local package. CI runs formatting checks, `python -m pytest -q`, and package build without running live protected-site tests.

- [ ] **Step 3: Verify locally**

Run: `python -m pip install -e .[test]`
Expected: package installs without relying on sibling directories.

Run: `python -m pytest -q`
Expected: all deterministic tests pass and live tests are reported skipped.

Run: `python -m build`
Expected: wheel and source distribution are created successfully.

- [ ] **Step 4: Verify the Docker image on the provided Linux host**

Build the repository as its own Docker context, start it on a non-production port, call liveness/readiness, run the official dummy-key test, trigger a worker timeout test, and verify that the next request is handled by a replacement worker. Do not replace the historical production container during this verification.

- [ ] **Step 5: Final repository checks and commit**

Run: `git status --short`
Expected: only intentional files are present before the final commit.

```bash
git add Dockerfile compose.yaml README.md LICENSE .dockerignore .github tests/test_browser_integration.py
git commit -m "docs: make service deployable and testable"
```

Run: `git status --short`
Expected: no output.
