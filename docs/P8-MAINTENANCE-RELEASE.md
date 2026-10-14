# P8：备份与恢复、性能基线、代码模块拆分、版本迁移及 ARM64

## 状态及版本

基于 P7 `342b2b7` 开发。保留原单容器 FastAPI + Next.js 架构、原有 `./data:/data`、Telegram 账号和加密 Session、P4 可靠性账本、P6 配置版本及 P7 媒体组/历史补抓。
P8 Dashboard 版本 `1.2.0`，SQLite schema `3`。未明确验证新版本前不要直接删除旧数据。

## 1. 加密备份

系统设置 → 加密备份与恢复：
- 创建加密包：16–1024 字符独立密码，scrypt KDF（N=32768, r=8, p=1）+ AES-256-GCM；密码不存入设置/日志，服务器无办法重置备份密码。
- 包含 `dashboard.sqlite3`、`upstream/message_relay.sqlite3`、`encryption.key`、`jwt.key`、`upstream/` 自定义规则及其他普通数据文件。SQLite 通过官方 backup API 在线创建各库一致的快照，而不是复制仍打开的 WAL 数据库。
- 每份数据最大：原始 256 MiB，压缩+加密包 100 MiB，单文件 128 MiB，最多 500 个文件、12 份本地备份。过大时改用**停机后宿主机整目录备份**。
- 加密包扩展名 `.tgsb`，保存在 `data/backups/`，下载后应单独妥善保管，不要提交到 GitHub、公开网盘。支持下载、独立密码验证、删除、从其他实例上传加密包后验证并导入。
- 拒绝包含链接、路径逃逸、重复文件、超过上限或哈希不一致的备份。还原之前检查 SQLite `PRAGMA quick_check`。**不会还原任何 JSON 文本中所引用的外部服务器秘密**。
- **注意：各个 SQLite 数据库分别是一致性快照，并非多个数据库的全局同时快照。** 高并发转发时建议维护窗口先暂停任务、创建备份，以获得更稳定的一致性。

## 2. 恢复（冷启动、可回退）

1. 创建并下载**当前**新备份，再选择原备份，填原密码并单独确认 `RESTORE`。
2. Dashboard 先验证并暂存所有数据在 `data/.restore-stage/<operation-id>/`，不会热替换正在打开的 SQLite 文件和登录密钥。应用显示待恢复编号。
3. 由管理员手动执行 `docker compose restart dashboard`（在当前 Compose 目录）；容器 entrypoint 降权 UID 10001 后，**先运行 `python -m app.restore_boot`，成功才启动 Uvicorn**。
4. 启动前校验暂存哈希与完整性，复制所有待替换原文件（包括 WAL/SHM）到 `data/.restore-previous/<operation-id>/`，写入恢复日志。逐文件原子替换数据库、配置和密钥。清除旧 SQLite 的 WAL/SHM，防止回放到新数据。
5. 恢复完成后重新使用旧备份对应的 Dashboard 密码登录；如冷启动中断，下次启动先利用恢复日志回滚至替换前数据，防止部分数据库 + 新密钥的混搭。状态可在 UI 查看。
6. 备份原有数据目录中的其他未包含文件**不会被删除**；备份与还原均拒绝符号链接。为减少意外数据残留，跨服务器彻底迁移优先在全新空数据目录进行。
7. `data/.restore-previous/` 是含密钥的**明文**回退副本，与正在使用的 `data/` 一样必须严格保护（目录模式 0700），管理员确认运行正常后在停机维护窗口手动清理。

**重要限制**：使用 `ENCRYPTION_KEY` 或 `JWT_SECRET` 外部环境变量覆盖本地密钥时，Dashboard 禁止生成“独立可恢复备份”或执行暂存恢复。请使用停机整目录备份并确保外部变量完整同步。在线恢复操作不支持重启容器；必须由管理员操作。

## 3. 性能基线

系统设置 → 运行性能基线：查看当前主库、转发账本 SQLite 的只读查询延迟采样（p50 + 短样本最大值近似 p95）、文件大小、WAL 大小、磁盘可用量、当前进程峰值内存、CPU 时间和机器架构；点击“记录一次性能采样”本地持久保存最近 30 次。查询不读取 Telegram 消息正文，不启动任务，也不做有写入的压测。

指标用于同一设备、类似负载的**趋势比较**，不是容量压力测试，也不能代表 Telegram API 端到端延迟。不以绝对毫秒阈值作为 GitHub CI 必过项，避免共享 runner 的噪声。

## 4. 模块拆分与迁移

- 提取 `backend/app/panel_api.py` 作为独立面板身份验证与健康检查模块，维持旧 `/api/health`、`/api/panel/login`、`/api/panel/me`、`/api/panel/logout`、`/api/panel/password` 路径及 Cookie/JWT 行为。
- `backup_core.py` 独立于已加载的 ORM 密钥，供容器 entrypoint 冷启动恢复；`backup_api.py` 专门处理鉴权后的备份接口；`performance.py` 隔离度量计算；`schema_migrations.py` 集中处理 schema 版本和升级检查。
- `schema_migrations` 表记录 `P6`、`P7`、`P8` 三步，以及 SQLite `PRAGMA user_version=3`。老数据库先增量添加缺失的 `group_name` 后记录版本。遇到来自**更高**版本数据库必须拒绝启动，不能静默降级。
- `/api/maintenance/performance/schema` 只读显示版本记录。

## 5. ARM64 + GHCR

- 发布流水线只有在 **main 的 push CI 全部通过** 后运行，运行 Buildx + QEMU，发布同一 `latest` 和 `sha-<12位提交>` 的多架构清单：`linux/amd64` / `linux/arm64`。
- CI 独立 ARM64 job：用 QEMU 运行 ARM64 镜像内 Python，验证 native packages：`cryptography`、`pyrogram`、`tg_signer`、`fastapi`、`sqlalchemy`。原 AMD64 Docker Compose 冒烟测试继续。
- 多架构镜像不改变 Compose 的 `image` 字段；Docker 根据服务器 CPU 自动拉取对应镜像。
- GHCR 发布状态未成功前不可声称 ARM64 已可用。请以成功构建的 Buildx manifest 与运行日志为准。

## 建议升级/回滚步骤

在正式 VPS 的当前 Compose 目录：

```bash
docker compose stop dashboard
umask 077
mkdir -p backups
tar -czf "backups/data-before-p8-$(date +%Y%m%d-%H%M%S).tar.gz" data
docker compose pull dashboard
docker compose up -d --wait dashboard
docker compose logs --tail=120 dashboard
```

如需回滚版本，**先停容器、恢复升级前完整 `data/`、改回通过测试的旧版本镜像，再启动**。不能只替换 SQLite 而留下新密钥与旧 Session。切勿让不同容器共享并发写入同一份 Session/数据库。

## 自动化与人工验收

Ruff + pytest、原执行器全部回归、TypeScript/Next、Playwright 维护页面、AMD64 Docker 冒烟、ARM64 QEMU 二进制依赖测试、Compose 原数据挂载保持。备份加密、错误密码、包篡改、备份导入、暂存、冷启动恢复、中途中断回滚、API 鉴权及 schema 迁移均有独立测试。真实 ARM64 VPS 上的 Telegram 登录、Bot 回执和持续运行仍建议在用户的实际环境验证。
