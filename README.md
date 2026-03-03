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
git clone git@codeup.aliyun.com:62694a075b46541dd2fed596/agentpod.git
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

编辑 `.env`，填入真实的 Provider API Key。至少需要一个 Provider（如火山引擎）：

```
VOLCENGINE_API_KEY=你的密钥
```

如果需要通过 HTTP API 管理用户（而非仅通过 CLI），还需配置 Admin Key：

```
# 推荐使用 https://www.uuidgenerator.net/version4 在线生成 UUID
AGENTPOD_ADMIN_KEY=你的UUID
```

> Admin Key 用于 `/v1/admin/*` 管理接口的鉴权。不配置则管理接口返回 501，不影响其他功能。

定时任务默认启用，如需调整可配置：

```
# AGENTPOD_CRON_ENABLED=true
# AGENTPOD_CRON_MAX_CONCURRENT=5
# AGENTPOD_CRON_TICK_INTERVAL=60
# AGENTPOD_CRON_SYNC_INTERVAL=300
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

### 7.5 配置共享层（可选）

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

### 8. 创建用户

```bash
uv run agentpod user create testuser
```

记下输出的 API Key（`sk-` 开头），后续请求需要用到。

查看数据库确认：

```bash
sqlite3 data/registry.db ".mode column" ".headers on" "SELECT id, api_key, is_active, created_at FROM users;"
```

### 9. 启动服务

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

### 10. 配置沙箱（BashTool 隔离）

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

### 11. 验证

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

### 12. 日常运维

```bash
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
├── deploy/              # 部署文件（service、.env.example）
├── example_cwd/         # 示例模板（仅供参考/测试）
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
