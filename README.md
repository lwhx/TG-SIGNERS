# TG-SIGNERS · Telegram 签到与消息转发 Dashboard

**Dashboard v1.2.0 · 单容器 · 中文界面 · Linux AMD64 / ARM64**

[项目仓库](https://github.com/lwhx/TG-SIGNERS) · [GHCR 镜像](https://github.com/lwhx/TG-SIGNERS/pkgs/container/tg-signers) · [CI](https://github.com/lwhx/TG-SIGNERS/actions/workflows/ci.yml) · [版本记录](CHANGELOG.md)

TG-SIGNERS 是一个集 Telegram 多账号管理、Bot 签到、规则自动化、通用消息监控与转发、故障处置于一体的 Web 控制台。**前端、FastAPI、Telegram 客户端与执行器封装在一个 Docker 容器中**，不需要额外的 Node、Redis 或数据库服务。持久化使用 SQLite 和宿主机 `./data` 绑定目录。

本项目在 [dongbo501/TG-SIGNER](https://github.com/dongbo501/TG-SIGNER) Dashboard 基础上改造，内置执行器来自 [amchii/tg-signer](https://github.com/amchii/tg-signer) 固定版本，并新增自有的 `relay_message` 消息转发与持久化投递账本。**这是改造版，不要将原作者仓库的 Compose 或镜像地址与本仓库混用。**

> **发布状态**：v1.2.0 对应的主分支 CI 和 GHCR 双架构发布工作流已经成功执行。更新前仍应确认当前目标镜像可拉取，并完整备份现有数据。真实 Telegram、115 Bot 与外部通知需要在自己的账号环境中验收。

## 功能概览

| 模块 | 主要能力 | 页面 |
| --- | --- | --- |
| 账号与签到（P0） | 多账号验证码 / 2FA 登录、Session 导入、定时与手动签到、随机延时、响应匹配 | 账号、签到任务 |
| 通用转发（P1–P4） | 多来源→多目标；forward、copy、text、extract、template；正则、去重、投递账本、安全重试 | 自动化与转发、转发记录 |
| 故障处理（P5） | 故障中心、重试排期、监听状态、CSV 元数据导出、可选恢复通知 | 故障中心 |
| 管理效率（P6） | 管理分组、任务复制、批量启停和删除、完整转发模拟预览、配置历史与回滚 | 签到任务、自动化与转发 |
| 转发增强（P7） | 115 Bot 回复结果追踪、媒体组整组投递、按批次手动历史消息补抓 | 转发增强 |
| 维护发布（P8） | 加密备份导入导出、冷启动恢复、性能采样、数据库迁移记录、AMD64/ARM64 镜像 | 系统设置 |
| 其他工具 | Telegram 文件夹 / 话题、成员查询、定时消息、Python Handler、AI 能力和多渠道通知 | 工具箱、系统设置 |

**状态语义要分清**：Telegram 返回成功，只说明消息已发送到目标，不代表 115 Bot 已转存成功；P7 只在 Bot 对原消息作出可明确关联的回复时才识别转存结果。投递结果不确定时，不会为了追求“成功率”盲目重复发送。

详细说明：[P4 可靠性](docs/P4-RELIABILITY.md) · [P5 故障中心](docs/P5-OPERATIONS.md) · [P6 配置管理](docs/P6-TASK-MANAGEMENT.md) · [P7 转发增强](docs/P7-RELAY-EXPANSION.md) · [P8 维护发布](docs/P8-MAINTENANCE-RELEASE.md) · [完整功能映射](docs/FEATURES.md)

## 环境要求

- 支持 Docker Engine 和 Docker Compose v2 的 Linux 服务器（`linux/amd64` 或 `linux/arm64`）。
- 服务器能够访问 GHCR 和 Telegram；部分地区需要独立的网络代理。
- 新部署建议有可持久化的本地磁盘；不要将 `data/` 放在临时目录。
- **一个数据目录只运行一个 Dashboard 容器、一个 Uvicorn worker。** 不得同时让新旧项目连接同一份 SQLite / Telegram Session。

## 1. 全新安装

从**本仓库**获取 Compose，避免误用原作者的镜像：

```bash
git clone https://github.com/lwhx/TG-SIGNERS.git
cd TG-SIGNERS
docker compose config --quiet
docker compose pull dashboard
docker compose up -d --wait dashboard
docker compose ps
```

当前 Compose 的服务名是 `dashboard`，容器名是 `tg-signer-dashboard`，镜像为：

```text
ghcr.io/lwhx/tg-signers:latest
```

默认 Compose 关键配置如下（完整内容请以 [docker-compose.yml](docker-compose.yml) 为准）：

```yaml
services:
  dashboard:
    image: ghcr.io/lwhx/tg-signers:latest
    container_name: tg-signer-dashboard
    ports:
      - "127.0.0.1:8999:8999"
    environment:
      TZ: "Asia/Shanghai"
      COOKIE_SECURE: "false"
      ADMIN_PASSWORD: ""
    volumes:
      - ./data:/data
```

- 左侧 `127.0.0.1:8999` 是**宿主机监听地址与端口**；右侧 `8999` 是容器内部端口。
- 默认只监听服务器本机回环地址，**无法直接通过服务器公网 IP 的 8999 端口访问**。
- `./data` 以 **Compose 文件所在目录**为基准；换部署目录不会自动继承旧目录里的账号、任务和 Session。
- `ADMIN_PASSWORD` 只在首次初始化空数据目录时有效；以后修改这个值不会覆盖已保存的管理密码。

### 首次登录

留空 `ADMIN_PASSWORD` 时自动生成初始密码：

```bash
cat data/initial-password.txt
# 或
docker compose exec dashboard cat /data/initial-password.txt
```

远程管理推荐使用 HTTPS 反向代理；临时查看可在自己的电脑建立 SSH 隧道：

```bash
ssh -L 8999:127.0.0.1:8999 root@服务器IP
```

随后在**自己的电脑浏览器**打开 `http://127.0.0.1:8999`。若本机 8999 也被占用，可把 SSH 命令最前面的本地端口改成其他空闲端口。

登录后先修改管理密码，再添加 Telegram 账号、测试连接、创建一个小范围任务进行实际验证。验证码、2FA 密码不会保存为持久配置；账号 Session 和相关凭据在数据目录中加密存储。

### 宝塔 / AxisNow / HTTPS 反向代理

默认推荐保持 `127.0.0.1:8999:8999`，让与 Docker 位于**同一台服务器**的代理转发到 `http://127.0.0.1:8999`。如果代理与 Docker 不在同一网络命名空间，需另行配置可达的安全源站地址，不能把远端代理的 `127.0.0.1` 当成 Docker 宿主机。

正式通过 HTTPS 访问时，将 Compose 内 `COOKIE_SECURE` 设为 `"true"`，代理保留 Host、Cookie 并支持 WebSocket；使用纯 HTTP 本地调试时保留 `"false"`。不要为了图方便直接将未受保护的管理面板暴露到公网。

## 2. 8999 端口被占用：新旧容器冲突

如果启动失败并提示：

```text
Bind for 127.0.0.1:8999 failed: port is already allocated
```

**先检查是否仍运行着原作者或之前部署的 TG-SIGNER。不要立即删除当前的数据目录。**

```bash
docker ps --format 'table {{.Names}}\t{{.Ports}}'
sudo ss -ltnp | grep ':8999'
docker compose ps
```

确认占用端口的确实是**不再需要运行的旧容器**后，在不依赖该旧服务的前提下停止它：

```bash
docker stop OLD_CONTAINER_NAME
docker compose up -d --wait dashboard
```

这里的 `OLD_CONTAINER_NAME` 需要替换为实际查到的名称，**不要停止不认识的其他容器**。如果旧版有账号、任务或 Session，先备份其实际挂载目录；**停止/删除容器并不等于把旧数据迁移到新版**。

如果需要同时运行两套彼此独立的 Dashboard，可以只修改其中一套的宿主机端口，例如：

```yaml
ports:
  - "127.0.0.1:19099:8999"
```

随后更新 SSH 隧道或 HTTPS 反向代理的源站端口。请确保两套容器使用**不同的容器名、不同的宿主机端口及不同的 `data/` 目录**，且不会重复使用同一份 Telegram Session 运行任务。

**禁止用 `docker compose down -v` 或 `rm -rf data` 作为端口冲突的排障手段。**

## 3. 数据目录和安全升级

运行数据主要包括：

```text
data/
├── dashboard.sqlite3               # 账号、任务、设置、执行记录、配置版本
├── encryption.key                   # 账号 Session 等凭据的加密密钥
├── jwt.key                          # 登录令牌相关密钥
├── initial-password.txt             # 仅首次初始化存在，修改密码后删除
├── upstream/
│   ├── message_relay.sqlite3        # 持久投递、去重、Bot 回执与媒体组
│   └── ...                          # 规则状态及自定义插件
├── backups/                         # P8 加密备份 .tgsb
└── .restore-previous/               # 恢复时可能留下的明文回退副本
```

首次部署后的部分文件或目录需使用对应功能才会出现。**`data/` 不进入 Git、不包含在 Docker 镜像中**；尤其要同时保留 `dashboard.sqlite3`、`encryption.key`、`jwt.key` 和 `upstream/`。冷启动回退目录中可能含明文密钥，必须像正式数据一样保护。

### 升级已有安装

在**当前实际部署所用的 Compose 目录**执行。升级前确认 `pwd`、容器名与 `./data` 挂载路径，避免误在遗留项目目录操作。

```bash
pwd
docker compose ps
docker compose config --quiet

# 先停服务，做整个数据目录的一致性备份
docker compose stop dashboard
umask 077
mkdir -p backups
tar -czf "backups/data-before-upgrade-$(date +%Y%m%d-%H%M%S).tar.gz" data

# 确认备份存在后再更新
docker compose pull dashboard
docker compose up -d --wait dashboard
docker compose ps
docker compose logs --tail=100 dashboard
```

源码仓库用户也可运行 `./scripts/backup.sh` 做停机备份（脚本结束后会尝试恢复原运行状态），但它**不会自动替你执行 Docker 镜像更新**。

从旧版 `dongbo501/TG-SIGNER` 迁移时，必须先核对旧容器的持久化挂载路径并停掉旧实例；新镜像不会自动接管旧项目路径里的数据。不要在新旧项目分别运行时直接复制正在写入的 SQLite 文件。

### P8 加密备份与恢复

「**系统设置 → 加密备份与恢复**」支持：创建、下载、导入、校验、删除加密包，以及暂存恢复和取消待恢复。密码长度至少 16 字符，备份采用 scrypt + AES-256-GCM；请离线保存密码，遗失后无法解密恢复。

1. 创建或导入 `.tgsb` 备份，使用原备份密码验证内容。
2. 选择恢复会将内容**校验并暂存**，不会在服务运行中覆盖数据库。
3. 管理员确认无误后手动执行 `docker compose restart dashboard`；启动脚本在应用加载数据库和密钥前执行恢复。
4. 如恢复遇到中断，使用恢复日志和上次原数据快照进行回滚；恢复成功后核对登录、任务及转发记录。

在线加密备份存在体积和文件数量限制，多个 SQLite 文件分别生成一致性快照，**不保证跨数据库的同一时刻快照**。用于整机迁移或跨版本回滚时，仍优先采用停机后的完整 `data/` 备份。详细限制和回滚流程见 [P8 维护手册](docs/P8-MAINTENANCE-RELEASE.md)。

## 4. 日常管理与排障

```bash
docker compose ps                           # 检查状态
docker compose logs --tail=100 dashboard     # 最近日志
docker compose logs -f dashboard             # 持续日志
docker compose restart dashboard             # 重启现有容器
docker compose pull dashboard                # 拉取镜像
docker compose up -d --wait dashboard        # 更新/启动
docker compose down                          # 删除此项目容器和网络；保留 ./data
```

| 现象 | 优先检查 |
| --- | --- |
| `port is already allocated` | 用 `docker ps` 和 `ss` 检查旧容器占用；必要时修改宿主机端口，不改容器端口 |
| 浏览器无法通过公网 IP 访问 8999 | 默认仅绑定 `127.0.0.1`；使用 SSH 隧道或同机 HTTPS 反向代理 |
| 关闭浏览器后访问要求重新登录 | 检查 Cookie 安全标志是否匹配 HTTP/HTTPS、浏览器 Cookie 是否仍有效及 JWT 是否过期 |
| 更新后像全新安装、账号不见了 | 先确认 Compose 所在目录及 `./data:/data` 是否仍指向旧数据，**不要重新初始化或删除旧数据** |
| 任务无法收到消息 | 检查账号授权、监听状态、来源权限及转发规则；故障中心和转发记录可查看诊断信息 |
| 转发成功但 115 没有实际转存 | Telegram 送达与 Bot 完成不同；查看「转发增强」的 Bot 回复状态，并在 Bot 聊天中核实 |
| 更新后不健康或启动失败 | 查看 `docker compose logs`，检查数据库迁移版本与文件权限；回滚需使用升级前完整备份 |

默认应用健康端点：`/api/health`。管理员登录后可打开 `/api/docs` 查看 Swagger / OpenAPI 接口。

## 5. 安全和能力边界

- SQLite 使用 WAL，调度器和 Telegram Session 需要**单副本、单 worker**；不要用多容器“高可用”复制方式直接共享同一目录。
- 登录使用 Argon2 密码哈希、HttpOnly JWT Cookie 和加密 Session；首次部署后应尽快修改密码并配置 HTTPS 访问。
- 记录导出和 Bot 结果追踪不保存消息正文；但管理员可以通过正常授权的 Telegram 客户端访问聊天内容，部署和备份权限必须妥善控制。
- 115 Bot 回执追踪依赖 Bot 对原消息的明确回复。若对方 Bot 不采用回复关联，则只能人工确认；历史补抓严格限制来源、批次和去重条件。
- 真实 Telegram 登录、验证码接收、消息发送、外部 Bot 转存、通知渠道以及长时间运行稳定性，仍须在部署环境中实际验收。自动化测试使用隔离的数据与模拟 Telegram 客户端。

## 6. 架构与开发

```text
Docker Compose (1 service: dashboard)
        │ 127.0.0.1:8999 → 容器 8999
        ▼
FastAPI + WebSocket + Next.js 静态页面
        ├── panel_api / auth / telegram        登录及账号
        ├── engine / tg_signer                 调度、签到和自动化
        ├── relay / relay_history / relay_p7  消息转发、Bot 回执
        ├── relay_backfill / relay_operations 历史补抓与故障
        ├── task_admin                         分组、批量管理、配置版本
        ├── backup_core / backup_api          加密备份与冷恢复
        ├── schema_migrations / performance    数据迁移与性能采样
        └── data/                              SQLite、状态和加密密钥
```

前端：Next.js 14 静态导出、React、Tailwind CSS、Radix UI、TanStack Table、Zustand。后端：Python 3.12、FastAPI、SQLAlchemy、APScheduler、Kurigram（兼容 Pyrogram）。内置上游执行器固定到 `fc8905db2ee74e2d9afc135cce88b50be35b6212`（原版本 `0.9.1`），已增加项目自有 Handler；**不要直接把外部最新版执行器覆盖到运行容器**。

本地开发与测试：

```bash
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt -e './upstream/tg-signer[yaml,speedup]'
.venv/bin/pip install pytest pytest-asyncio ruff
npm --prefix frontend ci
npm --prefix frontend run build

.venv/bin/ruff check backend --select E4,E7,E9,F,I,B --ignore B008
.venv/bin/python -m pytest -q backend/tests
.venv/bin/python -m pytest -q upstream/tg-signer/tests
npm --prefix frontend run typecheck
cd frontend
npx playwright install chromium
npm run test:e2e
```

GitHub Actions 会在 PR 和 `main` 推送时运行后端、前端、Docker 和 ARM64 验证；**仅主分支 CI 成功后**，GHCR 发布工作流才自动构建 `linux/amd64` + `linux/arm64` 多平台镜像，并写入 `latest` 及 `sha-<12位提交SHA>` 标签。建议升级时记录镜像标签或 digest 以便回滚。

补充资料：[安装手册](release/INSTALL.txt) · [GHCR 发布与迁移](docs/GHCR-DEPLOY.md) · [架构说明](docs/ARCHITECTURE.md) · [完整功能表](docs/FEATURES.md) · [验证说明](docs/VERIFICATION.md)

## 许可证和致谢

感谢 [dongbo501/TG-SIGNER](https://github.com/dongbo501/TG-SIGNER) Dashboard 和 [amchii/tg-signer](https://github.com/amchii/tg-signer) 上游执行器。第三方代码和许可证按各自仓库及本仓库保留的原始声明执行。
