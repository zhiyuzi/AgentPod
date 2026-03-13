# AgentPod

通用 AI Agent 运行时中间件。CWD 即边界，Runtime 即主权。

## 部署指南

> 目标环境：Ubuntu 24.04 LTS（阿里云 ECS 或同类云主机）
> 前置条件：root SSH 访问、已配置 Git SSH 密钥

### 1. 系统依赖

```bash
sudo apt update && sudo apt install -y git curl sqlite3
```

Python 3.12+ 是 Ubuntu 24.04 自带的，无需额外安装。

### 2. 安装 uv（Python 包管理器）

国内服务器从 GitHub 下载会很慢，推荐用阿里云 PyPI 镜像：

```bash
pip3 install uv -i https://mirrors.aliyun.com/pypi/simple/ --break-system-packages
```

> `--break-system-packages` 是 Ubuntu 24.04 的 PEP 668 要求，root 装全局工具没有副作用。

验证：

```bash
uv --version
```

### 3. 克隆项目

```bash
cd /opt
git clone git@github.com:zhiyuzi/AgentPod.git
cd /opt/agentpod
```

### 4. 安装依赖

```bash
uv sync
```

### 5. 配置环境变量

```bash
cp deploy/.env.example .env
```

编辑 `.env`，填入真实的 Provider API Key。至少需要一个 Provider：

```
# 火山引擎
VOLCENGINE_API_KEY=你的密钥

# 或智谱
ZHIPU_API_KEY=你的密钥
```

如果需要通过 HTTP API 管理用户（而非仅通过 CLI），还需配置 Admin Key：

```
# 推荐使用 https://www.uuidgenerator.net/version4 在线生成 UUID
AGENTPOD_ADMIN_KEY=你的UUID
```

> Admin Key 用于 `/v1/admin/*` 管理接口的鉴权。不配置则管理接口返回 501，不影响其他功能。

如果需要在 query/cron 完成后向外部系统推送事件通知（Webhook），配置：

```
AGENTPOD_WEBHOOK_URL=https://api.example.com/webhooks/agentpod
AGENTPOD_WEBHOOK_SECRET=whsec_你的随机密钥
```

> `WEBHOOK_URL` 为空则不推送，不影响其他功能。`WEBHOOK_SECRET` 用于 HMAC-SHA256 签名，接收方据此验证事件来源。详见下方"Budget & Webhook"章节。

定时任务默认启用，如需调整可配置：

```
# AGENTPOD_CRON_ENABLED=true
# AGENTPOD_CRON_MAX_CONCURRENT=5
# AGENTPOD_CRON_TICK_INTERVAL=60
# AGENTPOD_CRON_SYNC_INTERVAL=300
# AGENTPOD_CRON_MIN_INTERVAL=3600
```

### 6. 运行测试（可选）

```bash
uv run pytest -v
```

全部测试通过即表示环境正常。

### 7. 初始化数据

```bash
# Preflight 检查（自动创建 data/ 目录和 registry.db）
uv run agentpod check

# 准备用户模板
# 测试阶段可以用自带的示例模板：
cp -r example_cwd data/template

# 生产环境应该用你自己的 Agent 定义仓库：
# git clone your-agent-repo.git data/template

# 再次检查，确认 template ✓
uv run agentpod check
```

### 8. 配置共享层（可选）

共享层允许多个用户共享同一套 skills 和 AGENTS.md，无需为每个用户复制一份。

```bash
# 方案 A：从现有 template 创建 shared（推荐）
cp -r data/template data/shared

# 方案 B：使用独立的 Agent 定义仓库
git clone your-shared-repo.git data/shared

# 方案 C：不使用共享层（向后兼容，跳过此步骤即可）
```

共享层规则：
- `data/shared/` 目录存在即启用，不存在即禁用
- 用户有同名 skill/AGENTS.md 时，用户版本优先（完全覆盖）
- 更新 `data/shared/` 后立即对所有用户生效，无需重启服务
- 如需自定义路径：在 `.env` 中设置 `AGENTPOD_SHARED_DIR=/path/to/shared`

### 9. 创建用户

```bash
uv run agentpod user create testuser
```

记下输出的 API Key（`sk-` 开头），后续请求需要用到。

查看数据库确认：

```bash
sqlite3 data/registry.db ".mode column" ".headers on" "SELECT id, api_key, is_active, created_at FROM users;"
```

### 10. 配置 Budget & Webhook（可选）

AgentPod 支持持久余额（Budget）和事件通知（Webhook）。Budget 用于控制用户的总消费上限，Webhook 用于在 query/cron 完成后向外部业务系统推送事件。

#### Budget（持久余额）

每个用户有一个 `budget` 字段（单位：RMB，与 cost 单位一致）。新用户默认 budget = 0，此时所有请求会被 403 拒绝。需要先充值才能使用：

```bash
# 通过 Admin API 充值（累加）
curl -X POST http://localhost:8000/v1/admin/users/testuser/budget \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -H "Content-Type: application/json" \
  -d '{"amount": 10.0}'

# 或通过 CLI
uv run agentpod user budget testuser --add 10.0

# 查看余额
uv run agentpod user budget testuser
```

每次 query/cron 完成后，系统自动从 budget 中扣除本次 cost。余额归零时返回 403 `Budget exhausted`。

#### Webhook（事件通知）

配置 `AGENTPOD_WEBHOOK_URL` 后，以下事件会以 HTTP POST 推送到该 URL：

| 事件类型 | 触发时机 |
|----------|----------|
| `query_done` | 每次 query 完成（含 cost、余额、token 用量） |
| `cron_done` | 每次 cron 任务完成 |
| `budget_exhausted` | 用户余额归零 |

推送失败会重试 4 次（间隔 0s → 5s → 30s → 300s），全部失败后写入死信表（dead letters），可通过 Admin API 查看和重试。

#### Webhook 签名验证

每个 webhook 请求携带以下 headers：

| Header | 说明 |
|--------|------|
| `x-agentpod-signature` | `sha256=` + HMAC-SHA256(secret, `{timestamp}.{body}`) |
| `x-agentpod-event-id` | 事件唯一 ID（`evt_` 前缀） |
| `x-agentpod-timestamp` | Unix 时间戳（参与签名计算） |

签名计算方式：将 `x-agentpod-timestamp` 和 HTTP body 用 `.` 拼接后做 HMAC-SHA256。接收方验证伪代码：

```python
import hmac, hashlib

def verify_signature(body: bytes, secret: str, timestamp: str, signature_header: str) -> bool:
    sig_payload = f"{timestamp}.{body.decode()}"
    expected = "sha256=" + hmac.new(
        secret.encode(), sig_payload.encode(), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header)
```

> 生产环境务必配置 `AGENTPOD_WEBHOOK_SECRET` 并在接收端验证签名，防止伪造事件。不配置 secret 时签名仍会发送，但无法起到防伪作用。

### 11. 启动服务

#### 方式 A：前台运行（调试用）

```bash
uv run agentpod serve --host 0.0.0.0 --port 8000
```

#### 方式 B：systemd 托管（生产用）

```bash
# 创建系统用户（不可登录，仅用于服务降权运行）
sudo useradd -r -s /usr/sbin/nologin -d /opt/agentpod agentpod

# 给 agentpod 用户 data 目录权限
sudo chown -R agentpod:agentpod /opt/agentpod/data

# 安装并启动服务
sudo cp deploy/agentpod.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentpod

# 检查状态
sudo systemctl status agentpod
journalctl -u agentpod -f
```

### 12. 配置 Nginx 反向代理（推荐）

直接暴露 8000 端口不安全，推荐用 Nginx 反代：

```bash
sudo apt install -y nginx
sudo cp deploy/nginx.conf /etc/nginx/sites-available/agentpod
sudo ln -s /etc/nginx/sites-available/agentpod /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

配置后所有请求走 Nginx 80 端口，无需暴露 8000。可在防火墙关闭 8000 端口的外部访问：

```bash
# 仅允许本机访问 8000
sudo ufw allow 80/tcp
sudo ufw deny 8000/tcp
```

> 有域名后在 `deploy/nginx.conf` 中补上 `server_name` 和 TLS 证书配置，即可升级为 HTTPS/WSS。

### 13. 配置沙箱（BashTool 隔离）

BashTool 使用 Linux namespace + pivot_root 实现沙箱隔离。Ubuntu 24.04 的 AppArmor 默认限制非特权用户创建 user namespace，需要放开：

```bash
# 临时生效
sudo sysctl -w kernel.apparmor_restrict_unprivileged_userns=0

# 永久生效（重启后仍有效）
echo "kernel.apparmor_restrict_unprivileged_userns=0" | sudo tee /etc/sysctl.d/99-userns.conf
sudo sysctl --system
```

> 这个参数允许 agentpod 用户创建 user namespace，是沙箱工作的前提条件。沙箱本身通过 namespace 限制进程权限，不会引入安全风险。

配置后重启服务：

```bash
sudo systemctl restart agentpod
```

#### 资源限制（可选，推荐）

沙箱默认只做文件系统隔离，不限制 CPU/内存/进程数。如需防止单个命令耗尽服务器资源（如 `while True` 或 fork bomb），可启用 cgroups 资源限制：

```bash
# 启用 agentpod 用户的 lingering（允许用户级 systemd 在无登录时运行）
sudo loginctl enable-linger agentpod
```

在 `.env` 中配置：

```bash
AGENTPOD_SANDBOX_MEMORY_MAX=256M
AGENTPOD_SANDBOX_CPU_QUOTA=50%
AGENTPOD_SANDBOX_PIDS_MAX=64
```

> 不配置则不启用 cgroups 限制，行为与之前版本一致。限额是静态的，配合准入控制的并发限制使用。换更大的机器时调并发数，不需要改限额。

### 14. 验证

开另一个终端窗口：

```bash
# 健康检查
curl http://localhost:8000/v1/health
# 期望输出: {"status":"ok"}

# 发起对话（替换 sk-xxx 为实际 Key）
curl -N http://localhost:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"content": "你好，请用一句话介绍你自己"}'

# 查看用量
uv run agentpod usage testuser
```

### 15. 配置定时任务（可选）

定时任务通过用户 CWD 中的 TASK.md 文件定义，路径为 `.agents/cron/{name}/TASK.md`。

#### TASK.md 格式

YAML frontmatter + Markdown body：

````markdown
---
name: daily-report
description: 生成每日数据汇总报告
schedule: "0 9 * * *"
timezone: Asia/Shanghai
enabled: true
timeout: 300
max_turns: 20
model: doubao-seed-1-8-251228
---

请分析今天的数据变化，生成一份简洁的日报。将报告写入 reports/ 目录。
````

| 字段 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| name | 是 | — | 必须与目录名一致 |
| description | 是 | — | 一句话描述 |
| schedule | 是 | — | 5 字段 cron 表达式 |
| timezone | 否 | Asia/Shanghai | IANA 时区 |
| enabled | 否 | true | 是否启用 |
| timeout | 否 | 1200 | 最大执行时间（秒） |
| max_turns | 否 | 100 | 最大 agentic loop 轮数 |
| model | 否 | 用户 default_model | LLM 模型 |

Frontmatter 下方的 Markdown 正文即为发给 LLM 的 prompt。

#### 创建示例

```bash
# 在用户 CWD 中创建定时任务
mkdir -p data/users/testuser/.agents/cron/health-check
cat > data/users/testuser/.agents/cron/health-check/TASK.md << 'EOF'
---
name: health-check
description: 每小时检查服务状态
schedule: "0 * * * *"
---

检查当前目录下所有服务的运行状态，如有异常写入 alerts/ 目录。
EOF

# 同步到数据库
uv run agentpod cron sync testuser

# 确认任务已注册
uv run agentpod cron list testuser
```

> 定时任务是纯用户级的，每个用户独立管理自己的任务。共享层不包含 cron 定义。

### 16. 配置 Edge Gateway（可选）

Edge Gateway 允许云端 Runtime 调用用户本地机器上的工具（如浏览器自动化、本地文件操作）。用户本地运行一个 Edge Agent，通过 WebSocket 反向连接到服务端。

#### 服务端配置

Edge 工具的启用/禁用通过共享层的 `config.toml` 全局控制：

```bash
mkdir -p data/shared/.agents
cp deploy/config.toml.example data/shared/.agents/config.toml
# 按需编辑，enabled = false 可全局禁用特定工具
```

不创建配置文件 = 全部工具启用（默认行为）。配置仅控制过滤，工具的实际定义和执行逻辑在 Edge Agent 侧。

#### 客户端连接

项目自带示例 Edge Agent（`example_edge/`），用户复制到本地机器后即可运行：

```bash
pip install websockets
python -m example_edge ws://服务器IP/v1/edge/connect sk-xxx
# 看到 "Connected as xxx" 表示连接成功
```

`example_edge/` 内置了一个 `create_file` 演示工具，可在此基础上扩展自定义工具（参考 `example_edge/tools.py`）。

> Edge Agent 是一个轻量 Python 程序，负责接收云端的工具调用请求并在本地执行。断线后自动重连。如未配置 Nginx，使用 `ws://服务器IP:8000`。

#### 验证

Edge Agent 连接后，发送一个会触发本地工具的 query：

```bash
curl -N http://服务器IP:8000/v1/query \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-xxx" \
  -d '{"content": "请在当前目录创建一个 hello.txt 文件，内容写 Hello from Edge"}'
```

预期：SSE 流中出现 `edge_create_file` 工具调用，Edge Agent 终端显示执行日志，本地出现 `hello.txt`。

> 如果 Edge Agent 未连接，LLM 看不到 `edge_*` 工具，会使用内置工具正常工作，不影响现有功能。

### 17. 日常运维

```bash
# 查看运行时状态（需服务运行中 + .env 中配置 AGENTPOD_ADMIN_KEY）
uv run agentpod stats

# 查看日志
journalctl -u agentpod -f

# 升级代码
cd /opt/agentpod && git pull && uv sync
sudo systemctl restart agentpod

# 升级 Agent 模板（如果是独立仓库）
cd /opt/agentpod/data/template && git pull
sudo systemctl restart agentpod

# 升级共享层（如果是独立仓库）
cd /opt/agentpod/data/shared && git pull
# 无需重启！下一次 query 自动生效
```

## 目录结构

```
/opt/agentpod/           # 代码仓库（git 管理）
├── agentpod/            # 源码
├── deploy/              # 部署文件（service、.env.example、config.toml.example）
├── example_cwd/         # 示例 CWD 模板（仅供参考/测试）
├── example_edge/        # 示例 Edge Agent（复制到本地机器运行）
├── data/                # 运行时数据（.gitignore 排除）
│   ├── registry.db      # 用户数据库
│   ├── template/        # 用户模板（可以是独立 git 仓库）
│   ├── shared/          # 共享层（可选，存在即启用）
│   └── users/           # 各用户的独立工作目录
└── .env                 # 环境变量（.gitignore 排除）
```

## 设计要点

- **代码与数据分离**：`data/` 被 `.gitignore` 排除，`git pull` 升级代码不会影响用户数据和模板
- **模板与引擎分离**：`data/template/` 可以是独立的 git 仓库，有自己的版本管理和升级路径
- **用户隔离**：每个用户有独立的工作目录（从 template 复制），互不影响
- **服务降权**：systemd 以 `agentpod` 系统用户运行服务，即使被攻破也不会获得 root 权限
- **共享层**：`data/shared/` 提供平台级 skills 和 AGENTS.md，零拷贝共享，热更新无需重启

## 压力测试

项目自带并发压测脚本 `tests/stress_test.sh`，用于测试服务器在不同并发量下的表现。

```bash
# 用法: bash tests/stress_test.sh <api_key> [max_concurrency] [step] [start_from]

# 基础测试：从 5 开始，每次加 5，测到 30
bash tests/stress_test.sh sk-xxx 30 5

# 高并发测试：从 40 开始，每次加 10，测到 80
bash tests/stress_test.sh sk-xxx 80 10 40

# 精细测试：从 20 开始，每次加 1，测到 35
bash tests/stress_test.sh sk-xxx 35 1 20
```

测试前需要调高用户并发限制（默认为 2）：

```bash
uv run agentpod user config testuser '{"max_concurrent": 80}'
```

配合监控脚本观察系统资源：

```bash
# 另开一个终端，持续记录系统状态到文件
while true; do
  echo "=== $(date '+%H:%M:%S') ===" >> ~/monitor.log
  uptime >> ~/monitor.log
  free -h | grep Mem >> ~/monitor.log
  ss -tnp | grep 8000 | wc -l | xargs -I{} echo "connections: {}" >> ~/monitor.log
  echo "" >> ~/monitor.log
  sleep 2
done
```

关注指标：
- `free` 的 Mem 行：内存占用是否接近上限
- `load average`：持续 >CPU 核数说明 CPU 饱和
- `connections`：当前活跃连接数
- 压测脚本的 HTTP 状态码：429 = 用户并发限制，503 = 系统资源耗尽
