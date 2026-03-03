# CLAUDE.md

AgentPod — 通用 AI Agent 运行时中间件。CWD 即边界，Runtime 即主权。

## 项目概览

- Python 3.12+，包管理用 uv，构建后端 hatchling
- Web 框架 FastAPI + uvicorn，HTTP 客户端 httpx
- 数据库 SQLite（`data/registry.db`）
- 测试框架 pytest + pytest-asyncio（`asyncio_mode = "auto"`）
- 部署目标：Ubuntu 24.04 LTS（阿里云 ECS 2C/1.6G），systemd 托管
- Git 远程仓库：codeup.aliyun.com

## 核心设计哲学

每个用户拥有一个独立的 CWD（当前工作目录），Runtime 实例绑定该 CWD 并接管其中的一切——sessions、skills、Agent 定义、业务数据。Gateway 层极薄，只做鉴权、准入控制和请求转发。

## 架构分层

```
Gateway（FastAPI）→ Runtime（AgentRuntime）→ CWD（用户目录）
```

- Gateway：HTTP 入口、API Key 鉴权、准入控制（资源/并发/预算）、SSE 流式推送、CWD 文件管理 API
- Runtime：绑定 CWD，管理 SessionManager / ToolRegistry / PromptManager / ContextManager / AgenticLoop
- CWD：一个目录 = 一个完整运行空间（AGENTS.md + .agents/skills/ + sessions/ + version + 业务数据）

## 目录结构

```
agentpod/              # 源码包
├── cron/              # 定时任务：发现(discovery)、同步(sync)、调度器(scheduler)
├── gateway/           # HTTP 层：路由(app)、认证(auth)、准入控制(admission)、SSE(sse)、CWD文件管理(cwd)、定时任务(cron)、自检(preflight)
├── runtime/           # Agent 运行时：主循环(loop)、会话(session)、上下文(context)、prompt组装(prompt)、runtime入口(runtime)
├── providers/         # LLM Provider 适配：基类(base) + 火山引擎(volcengine)
├── tools/             # 内置工具：bash, read, write, edit, grep, glob, web_fetch, web_search, ask_user, todo_write, list_skills, get_skill
├── sandbox/           # 沙箱（CWD 路径限制）
├── config.py          # 配置加载（环境变量 → dataclass）
├── db.py              # SQLite 数据库操作（users + usage_logs + cron_tasks + cron_runs 表）
├── cli.py             # CLI 入口（serve, check, init, user, usage, cron）
├── logging.py         # JSON 结构化日志
└── types.py           # 共享类型定义（RuntimeEvent, RuntimeOptions 等）
tests/                 # 测试（结构镜像 agentpod/）
deploy/                # 部署文件（agentpod.service, .env.example）
example_cwd/           # 示例 CWD 模板（仅供参考/测试）
.docs/                 # 内部文档（.gitignore 排除，不随代码发布）
├── spec-v1.0/         # v1.0 设计文档(design.md)、任务清单(tasks.md)、压测报告(stress-test-report.md)
├── naming.md          # Pod 命名由来
└── token_calc_report.md  # 火山引擎/智谱/MiniMax Token 计算能力调研
data/                  # 运行时数据（.gitignore 排除）
├── registry.db        # 用户数据库
├── template/          # 用户 CWD 模板（可以是独立 git 仓库）
├── shared/            # 共享层（可选，存在即启用）
└── users/             # 各用户的独立工作目录
```

## 常用命令

```bash
uv sync                          # 安装依赖
uv run pytest -v                 # 跑测试
uv run agentpod serve            # 启动服务
uv run agentpod check            # Preflight 检查
uv run agentpod user create <id> # 创建用户
uv run agentpod user config <id> '{"key": "value"}'  # 更新用户配置（JSON merge）
uv run agentpod usage <id>       # 查看用量（默认今日）
uv run agentpod usage <id> --month 2026-02  # 按月查看
uv run agentpod cron list <id>           # 列出用户定时任务
uv run agentpod cron runs <id>           # 执行历史
uv run agentpod cron runs <id> --task <name>  # 按任务过滤
uv run agentpod cron sync <id>           # 同步单个用户
uv run agentpod cron sync --all          # 同步所有用户
uv run agentpod cron disable <id> <name> # 禁用任务
uv run agentpod cron enable <id> <name>  # 启用任务
uv run agentpod cron delete <id> <name>  # 删除任务（软删除）
```

## HTTP API

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/query` | 发起对话（SSE 流式返回） |
| POST | `/v1/answer` | 回答 ask_user 提问 |
| GET | `/v1/sessions` | 列出会话 |
| GET | `/v1/sessions/{id}` | 会话详情 |
| POST | `/v1/sessions/{id}/fork` | 分叉会话 |
| GET | `/v1/context/{session_id}` | 上下文快照 |
| GET | `/v1/me` | 当前用户信息 |
| GET | `/v1/usage` | 当前用户用量 |
| GET/PUT/DELETE | `/v1/cwd/{path}` | CWD 文件读取/写入/删除 |
| POST | `/v1/cwd/` | CWD 创建文件或目录 |
| GET | `/v1/health` | 健康检查（无需鉴权） |

### Cron API（Bearer token 鉴权）

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/v1/cron/tasks` | 列出自己的定时任务 |
| GET | `/v1/cron/tasks/{name}` | 任务详情 + 最近执行记录 |
| POST | `/v1/cron/tasks/{name}/enable` | 启用任务 |
| POST | `/v1/cron/tasks/{name}/disable` | 禁用任务 |
| DELETE | `/v1/cron/tasks/{name}` | 删除任务（DB 软删除） |
| GET | `/v1/cron/runs` | 执行历史（可按 task 过滤） |
| GET | `/v1/cron/runs/{id}` | 单次执行详情 |
| POST | `/v1/cron/sync` | 手动触发 CWD→DB 同步 |

### Admin API（需 `AGENTPOD_ADMIN_KEY` 鉴权）

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/v1/admin/users` | 创建用户 |
| GET | `/v1/admin/users` | 列出所有用户 |
| GET | `/v1/admin/users/{id}` | 用户详情 |
| PATCH | `/v1/admin/users/{id}` | 更新配置（merge） |
| POST | `/v1/admin/users/{id}/disable` | 禁用用户 |
| POST | `/v1/admin/users/{id}/enable` | 启用用户 |
| POST | `/v1/admin/users/{id}/reset-key` | 重置 API Key |
| GET | `/v1/admin/users/{id}/usage` | 查看用量 |
| GET | `/v1/admin/stats` | 系统运行状态总览（CPU/内存/磁盘/并发/今日用量/cron） |
| GET | `/v1/admin/cron/tasks` | 所有用户的定时任务（可按 user_id 过滤） |
| GET | `/v1/admin/cron/runs` | 所有执行记录（可按 user_id/status 过滤） |
| POST | `/v1/admin/cron/tasks/{id}/disable` | 强制禁用任务 |
| POST | `/v1/admin/cron/tasks/{id}/enable` | 重新启用 |
| DELETE | `/v1/admin/cron/tasks/{id}` | 删除任务（DB 软删除） |
| POST | `/v1/admin/cron/sync` | 全量同步所有用户 |

## 数据库表

- `users`：id, api_key, cwd_path, config(JSON), is_active, created_at, updated_at
- `usage_logs`：user_id, session_id, model, turns, input/output/cached_tokens, cost_amount, duration_ms, created_at
- `cron_tasks`：id("{user_id}:{task_name}"), user_id, task_name, description, schedule, timezone, enabled, deleted, timeout, max_turns, model, content_hash, last_synced_at, next_run_at, created_at, updated_at
- `cron_runs`：id, task_id, user_id, task_name, session_id, status(running|completed|failed|timeout), started_at, finished_at, error_message, cost_amount, input/output_tokens, turns, duration_ms

用户 config JSON 字段：max_budget_per_session, max_budget_daily, max_turns, max_concurrent, default_model, allowed_models, disallowed_tools, context_window, features, writable_paths

## 编码约定

- 所有 `json.dumps()` 必须加 `ensure_ascii=False`（CJK 字符直出，不转义为 \uXXXX）
- SSE 事件格式：`event: xxx\ndata: {json}\n\n`，Anthropic 风格显式事件类型
  - `turn_complete` 携带累积 usage 和 cost（每轮推送，前端可实时展示）
  - `done` 携带最终 usage、cost 和 stop_reason（end_turn / max_turns / budget）
  - 客户端断开 SSE 连接即为"停止生成"，服务端自动清理资源并兜底写入 usage
- Provider 统一继承 `providers/base.py` 的 `BaseProvider`
- 工具统一继承 `tools/base.py` 的 `BaseTool`
- 配置通过环境变量注入：`AGENTPOD_*`（服务端）、`AGENTPOD_CRON_*`（定时任务）、`VOLCENGINE_*` / `ANTHROPIC_*` / `ZHIPU_*` / `MINIMAX_*`（Provider）
  - `AGENTPOD_SHARED_DIR=data/shared`（共享层目录路径，默认 `{data_dir}/shared`，存在即启用）
- Commit message 格式：`<type>(<scope>): <description>`，type: feat/fix/test/refactor/chore/docs
- JSON 结构化日志输出到 stdout，每条携带 user_id 和 session_id
- 测试中用 `tmp_path` fixture 创建临时 CWD，不依赖真实 `data/` 目录

## 架构要点

- 用户隔离：每个用户有独立 CWD（从 `data/template/` 复制），工具操作通过沙箱限制在 CWD 内。沙箱使用 `pivot_root`（非 chroot）+ `umount` 旧根实现文件系统隔离，防止 fd-based 逃逸。bind-mount 系统目录：`/bin`、`/usr`、`/lib`、`/lib64`、`/etc/alternatives`（update-alternatives symlink 链）、`/dev`，全部只读 + nosuid。`/proc` 在 pivot_root 后独立挂载，仅显示沙箱 PID
- 准入控制（`gateway/admission.py`）：全局信号量（默认 20，排队不拒绝）+ 用户级并发限制（默认 2，超限 429）+ 内存 >90% 返回 503 + 日预算检查
- Runtime 主循环（`runtime/loop.py`）：LLM 调用 → tool_use → 工具执行 → 再调用，直到 LLM 不再请求工具
- 会话持久化（`runtime/session.py`）：JSONL 追加写入，实时落盘
- 上下文管理（`runtime/context.py`）：Token 追踪，达到阈值（默认 70%）时触发压缩
- CWD 文件保护：.agents/、AGENTS.md、version、sessions/ 为系统保护路径，可读不可写
- 定时任务（`cron/`）：用户通过 `.agents/cron/{name}/TASK.md` 定义，CWD→DB 同步，asyncio 后台调度，独立信号量（默认 5），每用户同时 1 个 cron 任务，croniter 解析 + 时区支持
- 共享层（`data/shared/`）：与用户 CWD 形成两层 overlay（`有效视图 = shared + user CWD，user wins`）。`discover_skills(*dirs)` 多目录合并，后传入的目录优先级更高，每个 skill 带 `source` 字段（`"shared"` / `"user"`）。AGENTS.md 文件级 fallback（user 有则用 user，没有则用 shared）。沙箱通过 bind-mount 将 shared 内容只读挂载到沙箱内。排除列表 `_SHARED_EXCLUDE = {".agents/cron", "sessions", "version"}` 不从 shared 挂载。`AGENTPOD_SHARED_DIR` 配置或 `{data_dir}/shared` 自动检测，存在即启用
- 优雅停机：SIGTERM → 停止新连接 → 等待进行中 query 完成 → 超时强制退出（默认 30s）

## 注意事项

- `data/` 和 `.env` 被 gitignore，不要提交
- `.docs/` 也被 gitignore，是内部文档，不随代码发布
- 详细架构设计见 `.docs/spec-v1.0/design.md`（~1300 行）
- 实施任务清单见 `.docs/spec-v1.0/tasks.md`（52 个任务，5 个 Phase）
