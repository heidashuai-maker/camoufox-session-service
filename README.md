# Camoufox Session Service

一个独立的 Python 浏览器任务服务，通过精简的 HTTP API 调度 Camoufox。项目面向需要浏览器渲染、CAPTCHA 组件加载、页面 Challenge 状态检测以及浏览器 Session 复用的场景。

## 功能范围

- reCAPTCHA v2 复选框与音频 Challenge。
- Turnstile 最小组件和真实页面两种加载策略。
- 整页 Challenge 检测、页面状态归类与 Session 信息导出。
- 持久浏览器上下文，以及基于同一 Session 的后续请求。
- 有界任务队列、硬超时、进程树替换、Worker 回收与运行指标。

本项目不依赖历史 `turnstile-token-service`、Puppeteer、Selenium 或其他同级仓库。调用方不需要维护 Node.js 项目；底层 Playwright Python 包仍会携带自身的驱动运行时。

## 环境要求

- Python 3.11+
- Camoufox 浏览器二进制
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
| `POST` | `/v1/challenge/solve` | 检测整页 Challenge 并导出观察结果 |
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
  -d '{"url":"https://authorized.example/","waitSeconds":30,"returnHtml":true}'
```

可能返回 `solved`、`no_challenge`、`challenge_present`、`interactive_required`、`timeout` 或 `failed`。该接口报告浏览器实际观察到的状态，不承诺通过所有 Cloudflare Managed Challenge。

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
| `CAMOUFOX_SESSION_TTL_SECONDS` | `900` | 默认 Session 生存时间 |
| `CAMOUFOX_MAX_JOBS_PER_WORKER` | `50` | Worker 最大任务数 |
| `CAMOUFOX_MAX_WORKER_LIFETIME_SECONDS` | `1800` | Worker 最大生存时间 |
| `CAMOUFOX_MAX_WORKER_RSS_MB` | `1536` | Worker RSS 回收阈值 |
| `CAMOUFOX_HEADLESS` | `true` | `true`、`false` 或 `virtual` |

## 测试

```bash
python -m ruff check .
python -m ruff format --check .
python -m pytest -q
```

默认测试可重复执行，不访问受保护站点。可选浏览器集成测试使用 Cloudflare 官方 Dummy Sitekey：

```bash
RUN_BROWSER_TESTS=1 python -m pytest tests/test_browser_integration.py -q
```

Dummy Key 只验证浏览器启动、文档拦截、Widget 渲染、回调捕获和资源清理，不衡量真实站点通过率。真实验收测试必须在自有站点或明确授权的目标上单独执行。

## Docker

```bash
docker compose build
docker compose up -d
curl http://127.0.0.1:3000/health/ready
```

镜像不包含 Chromium 运行时。Compose 启用最小 `init` 进程回收浏览器后代进程。每个 Worker 子进程独占 Camoufox 实例；任务超过硬超时后，Supervisor 会终止完整进程树，创建替代 Worker，再让该槽位接收新任务。

## 能力边界

- Turnstile Token 通常短期、单次有效，仍需由站点服务端执行验证。
- 获取普通 Session Cookie 或 `__cf_bm` 不等于取得有效 `cf_clearance`。
- Managed Challenge 是否通过取决于站点策略、网络信誉、浏览器身份和交互要求，本项目不提供通过率保证。
- reCAPTCHA 音频识别依赖外部音频工具和语音识别服务，可能受到语言、速率限制和网络质量影响。
- 请只对自有系统或已明确授权的目标使用本服务。
