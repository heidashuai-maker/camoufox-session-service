# 中文文档与源码注释设计

## 目标

在不改变 API 合约、运行逻辑和项目结构的前提下，提高中文开发者阅读、部署和维护 `camoufox-session-service` 的效率。

## 设计原则

- 文档中文优先，命令、环境变量、API 路径、JSON 字段和状态值保持原样。
- 注释解释职责、边界和设计原因，不重复代码已经表达的动作。
- 只覆盖核心模块、关键类和复杂函数，不追求机械式注释覆盖率。
- 日志、异常文本和响应结构保持英文，避免破坏调用方、测试和问题检索。
- 不重构业务代码，不新增运行依赖，不改变公开接口。

## 修改范围

### README

将 `README.md` 完整翻译为中文，保留可复制执行的命令和请求示例。内容覆盖项目定位、依赖、API、Session 复用、配置、测试、Docker 和能力边界。

### 架构说明

新增 `docs/architecture.md`，用中文说明以下关系：

- FastAPI 接口如何把任务交给 Supervisor。
- Supervisor 如何管理有界队列、Worker、硬超时和进程替换。
- Worker 如何持有 Camoufox 实例与浏览器上下文。
- Session 如何绑定 Worker 代际、Cookie、User-Agent 和代理身份。
- Turnstile、reCAPTCHA 与页面 Challenge 的不同处理路径。

### 配置说明

在 `.env.example` 中为配置分组添加简洁中文说明，变量名和默认值不变。

### 源码注释

为 `src/camoufox_service` 下的核心运行模块添加中文模块 docstring；为以下对象补充职责型 docstring：

- 配置与 API 数据模型。
- 浏览器运行时与上下文管理。
- Session Registry。
- Supervisor、Worker Slot 与任务派发。
- Turnstile、reCAPTCHA、音频识别和页面 Challenge 的入口及关键辅助函数。

行内注释仅用于并发边界、硬超时、进程树回收、Worker 代际失效和页面拦截等不易从语句本身理解的逻辑。

## 不在范围内

- 不翻译 API 字段名、枚举值、HTTP 错误文本和日志字段。
- 不改变求解策略、超时行为、资源限制或浏览器参数。
- 不为测试函数逐个添加说明性注释。
- 不新增国际化框架或语言切换配置。

## 验收标准

- README 和架构说明可独立帮助中文开发者完成部署与接口调用。
- 核心模块、关键类和复杂函数具备简洁中文职责说明。
- Ruff 检查和格式检查通过。
- 默认 pytest 测试套件通过。
- sdist 与 wheel 构建通过。
- Git diff 不包含运行逻辑或 API 合约变化。
- 提交使用当前仓库配置的 `heidashuaui@gmail.com`，并直接推送到 `main`。
