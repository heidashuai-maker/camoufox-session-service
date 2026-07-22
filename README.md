# Camoufox Session Service

一个独立的 Python 浏览器任务服务，通过精简的 HTTP API 调度 Camoufox 与隔离的 Chromium Challenge Worker。项目面向需要浏览器渲染、CAPTCHA 组件加载、Cloudflare Managed Challenge 处理以及浏览器 Session 复用的场景。

## 功能范围

- reCAPTCHA v2 复选框与音频 Challenge。
- Turnstile 最小组件和真实页面两种加载策略。
- 独立 DrissionPage/Chromium Worker 复用长期浏览器，并用任务级 Context 处理整页 Cloudflare Managed Challenge。
- 持久浏览器上下文，以及基于同一 Session 的后续请求。
- 有界任务队列、硬超时、进程树替换、Worker 回收与运行指标。

本项目不依赖历史 `turnstile-token-service`、Puppeteer、Selenium、FlareSolverr 或其他同级仓库。组件任务和持久 Session 使用 Camoufox；整页 Challenge 使用独立的 DrissionPage/Chromium Worker，两个浏览器后端不共享生命周期。

## 环境要求

- Python 3.11+
- Camoufox 浏览器二进制
- Chromium 与可用的图形显示；Docker 镜像使用 Xvfb
- `ffmpeg` 和 `ffprobe`，用于 reCAPTCHA 音频格式转换

## 本地运行

Linux：

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[test]"
python -m camoufox fetch
python -m camoufox_service
```

Windows 激活虚拟环境：

```powershell
.\.venv\Scripts\Activate.ps1
```

服务默认监听 `http://127.0.0.1:3000`。接口文档位于 `/docs`，就绪检查位于 `/health/ready`。

## API

| 方法 | 路径 | 作用 |
| --- | --- | --- |
| `POST` | `/v1/turnstile/solve` | 加载 Turnstile 最小组件或真实页面组件 |
| `POST` | `/v1/recaptcha/v2/solve` | 处理 reCAPTCHA v2 复选框与音频 Challenge |
| `POST` | `/v1/challenge/solve` | 处理整页 Cloudflare Managed Challenge 并导出浏览器身份 |
| `POST` | `/v1/sessions` | 创建持久浏览器 Session |
| `GET` | `/v1/sessions` | 查看当前 Session |
| `POST` | `/v1/sessions/{sessionId}/request` | 在指定 Session 内发送请求 |
| `DELETE` | `/v1/sessions/{sessionId}` | 删除 Session 并关闭其浏览器上下文 |

### Turnstile 最小组件

```bash
curl -X POST http://127.0.0.1:3000/v1/turnstile/solve \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.test",
    "siteKey": "1x00000000000000000000AA",
    "strategy": "minimal"
  }'
```

`strategy: "minimal"` 会在保持目标 Origin 的前提下加载独立组件；`strategy: "page"` 会访问真实页面并读取页面已有的 Widget。接口接受类型化的 `action`、`cData`、`appearance`、`execution` 和 `language` 选项，不接受调用方注入任意 JavaScript。

### reCAPTCHA v2 复选框与音频挑战

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

当前只实现 reCAPTCHA v2 复选框与音频流程，不包含 v3 和 Enterprise。

### 整页 Challenge

```bash
curl -X POST http://127.0.0.1:3000/v1/challenge/solve \
  -H "Content-Type: application/json" \
  -d '{
    "url":"https://authorized.example/",
    "proxy":"socks5h://proxy.example:8501",
    "timeoutMs":120000,
    "returnHtml":false,
    "retainSession":true,
    "ttlSeconds":900
  }'
```

该接口由独立的 DrissionPage/Chromium Worker 执行，可能返回 `solved`、`no_challenge`、`blocked`、`cloudflare_error`、`timeout`、`browser_crashed` 或 `failed`。每个 Worker 延迟启动并复用一个 Chromium；每个任务创建独立 Browser Context，可分别指定无认证 HTTP、HTTPS 或 SOCKS 代理。`returnHtml` 默认是 `false`；开启后响应仍受 `WORKER_STREAM_LIMIT_BYTES` 限制。

默认情况下任务结束后会销毁 Context。设置 `retainSession:true` 后，成功结果包含 `sessionId`，原 Context 会保留到 TTL 到期或显式删除。后续 GET 请求可直接复用通过挑战时的 Chromium、代理、Cookie 和网络指纹：

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions/SESSION_ID/request \
  -H "Content-Type: application/json" \
  -d '{"method":"GET","url":"https://authorized.example/","returnHtml":true}'
```

Challenge Session 当前只支持 GET；删除方式仍为 `DELETE /v1/sessions/SESSION_ID`。如果 Context 销毁失败，则关闭当前 Chromium，并使绑定该 Worker generation 的 Session 失效。

成功结果包含实际 User-Agent 和 Cookie；复用 `cf_clearance` 时必须保持相同出口 IP、代理及 User-Agent。该接口提高已知 Managed Challenge 的通过能力，但不承诺固定通过率。

### 持久 Session

创建空浏览器上下文：

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions \
  -H "Content-Type: application/json" \
  -d '{"ttlSeconds":900}'
```

将求解结果中的 Cookie 放回服务，并保持相同 User-Agent 和代理身份：

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

使用返回的 `sessionId` 发送后续请求：

```bash
curl -X POST http://127.0.0.1:3000/v1/sessions/SESSION_ID/request \
  -H "Content-Type: application/json" \
  -d '{"method":"GET","url":"https://example.test","returnHtml":true}'
```

删除 Session：`DELETE /v1/sessions/SESSION_ID`。

Cookie、User-Agent 和代理身份属于同一浏览器身份，复用时必须保持一致。Session 会绑定创建它的 Worker ID 与 generation；如果该 Worker 重启，服务会使 Session 失效并返回 HTTP 410，而不是静默切换到新的浏览器身份。

## 配置

复制 `.env.example` 为 `.env`。`AUTH_TOKEN` 非空时，业务接口和指标接口要求：

```text
Authorization: Bearer <token>
```

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HOST` | `0.0.0.0` | HTTP 监听地址 |
| `PORT` | `3000` | HTTP 监听端口 |
| `AUTH_TOKEN` | 空 | 可选 Bearer Token |
| `CAMOUFOX_WORKERS` | `1` | Worker 子进程数量 |
| `CAMOUFOX_QUEUE_SIZE` | `8` | 等待队列容量 |
| `CAMOUFOX_TASK_TIMEOUT_SECONDS` | `120` | 单任务硬超时 |
| `WORKER_STREAM_LIMIT_BYTES` | `16777216` | Worker 单行 JSON 响应上限（16 MiB） |
| `CAMOUFOX_SESSION_TTL_SECONDS` | `900` | 默认 Session 生存时间 |
| `CAMOUFOX_MAX_JOBS_PER_WORKER` | `50` | Worker 最大任务数 |
| `CAMOUFOX_MAX_WORKER_LIFETIME_SECONDS` | `1800` | Worker 最大生存时间 |
| `CAMOUFOX_MAX_WORKER_RSS_MB` | `1536` | Worker RSS 回收阈值 |
| `CAMOUFOX_HEADLESS` | `true` | `true`、`false` 或 `virtual` |
| `CHALLENGE_WORKERS` | `1` | DrissionPage Challenge Worker 数量 |
| `CHALLENGE_QUEUE_SIZE` | `2` | Challenge 等待队列容量 |
| `CHALLENGE_TASK_TIMEOUT_SECONDS` | `180` | Challenge Supervisor 默认硬超时 |
| `CHALLENGE_MAX_JOBS_PER_WORKER` | `10` | Challenge Worker 最大任务数 |
| `CHALLENGE_MAX_WORKER_LIFETIME_SECONDS` | `900` | Challenge Worker 最大生存时间 |
| `CHALLENGE_MAX_WORKER_RSS_MB` | `2048` | Challenge Worker RSS 回收阈值 |
| `CHROMIUM_PATH` | `/usr/bin/chromium` | Chromium 可执行文件路径 |

## 测试

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

默认测试可重复执行，不访问受保护站点。两个可选浏览器集成测试分别验证 Camoufox Widget 链路和 DrissionPage Context 隔离链路：

```bash
RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser_integration.py -q
RUN_CHALLENGE_BROWSER_TESTS=1 python -m pytest tests/test_challenge_browser_integration.py -q
```

第一个测试使用 Cloudflare 官方 Dummy Sitekey，只验证浏览器启动、文档拦截、Widget 渲染、回调捕获和资源清理；第二个测试使用本地 Challenge Fixture，验证长期 Chromium 下创建、销毁任务 Context 的行为。两者都不衡量真实站点通过率。真实验收测试必须在自有站点或明确授权的目标上单独执行。

## Docker

```bash
docker compose build
docker compose up -d
curl http://127.0.0.1:3000/health/ready
```

镜像同时包含 Camoufox、Chromium、Xvfb 和 `tini`；即使不通过 Compose 启动，也会由最小 init 进程转发信号并回收浏览器后代进程。每个 Camoufox Worker 独占一个长期浏览器实例；每个 Challenge Worker 也延迟启动并复用一个 Chromium，但为每个任务创建和销毁独立 Browser Context。任务超过硬超时后，Supervisor 会终止完整进程树并创建替代 Worker。

Compose 的 `4g` 是容器内存上限，不是预分配内存。两个 `*_MAX_WORKER_RSS_MB` 是单个 Worker 的主动回收阈值；增加 Worker 数量时，应同时按实际峰值提高容器内存上限，避免容器先于 Worker 回收机制触发 OOM。

当前固定使用已通过目标站点验证的 `DrissionPage 4.1.0.0b14`，避免自动升级改变浏览器控制行为。该版本的包元数据声明为 BSD License；升级依赖前应重新检查许可证和真实站点回归结果。

## 能力边界

- Turnstile Token 通常短期、单次有效，仍需由站点服务端执行验证。
- 获取普通 Session Cookie 或 `__cf_bm` 不等于取得有效 `cf_clearance`。
- 即使取得 `cf_clearance`，普通 HTTP 客户端也可能因 TLS/网络指纹不同而被拒绝；优先复用 Challenge 返回的浏览器 `sessionId`。需要切换到 HTTP 客户端时，可尝试 `curl_cffi` 的 Chrome impersonation，但必须保持同一代理出口和 User-Agent，并导入完整 Cookie 集；仅复用 `JSESSIONID` 或单个 Cloudflare Cookie 不可靠。
- Managed Challenge 是否通过取决于站点策略、网络信誉、浏览器身份和交互要求；DrissionPage 后端不等同于稳定或百分之百通过。
- Challenge 后端暂不支持带用户名和密码的代理；无认证 HTTP、HTTPS、SOCKS 代理沿用结构化 `proxy` 模型。
- reCAPTCHA 音频识别依赖外部音频工具和语音识别服务，可能受到语言、速率限制和网络质量影响。
- 请只对自有系统或已明确授权的目标使用本服务。
