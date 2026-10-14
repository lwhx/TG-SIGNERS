# 自有 GHCR Dashboard 发布与安全迁移

## 设计原则

- 保留原 [dongbo501/TG-SIGNER](https://github.com/dongbo501/TG-SIGNER) 的 Next.js + FastAPI Dashboard、SQLite 数据库与登录认证。
- 改为 GitHub Actions 自动构建单容器镜像 `ghcr.io/lwhx/tg-signers:latest`，宿主机无需本地 Docker build。
- 数据目录继续使用 **`./data:/data`**，不会迁移为其他项目的 `/opt/tg-signer`。
- 原有 `dashboard.sqlite3`、`encryption.key`、`jwt.key`、`upstream/` 必须保持；密码哈希存储在 SQLite 中，`ADMIN_PASSWORD` 只在首次初始化时生效。

## 自动测试与发布

1. 每个 PR 和 `main` 推送运行 **CI**：后端及嵌入执行器测试、Next.js 类型检查与构建、Compose 验证、实际 Docker 构建与健康检查。
2. **仅在 main 的 push CI 全部成功后**，自动运行 **Publish GHCR Dashboard** 工作流，发布 `latest` 和不可变提交标记 `sha-<12位提交SHA>`。不会因 PR 或 fork 的提交直接发布镜像。
3. GitHub Actions 使用 `GITHUB_TOKEN` 与 `packages: write` 权限发布 GHCR 包。发布完成后确认镜像访问权限允许 VPS 拉取；公开仓库不代表包一定公开。
4. P8 扩展为 Linux amd64 与 Linux arm64 双架构；ARM64 必须先通过模拟运行与原生 Python 依赖 CI，才能随主分支自动发布。

## 从原版 Dashboard 迁移

**在原项目实际部署目录操作**，确保 `./data` 仍然指向原来的数据，不要先执行删除命令。

先停止旧容器，再完整备份数据：

```bash
docker compose stop dashboard
mkdir -p backups
chmod 700 backups
umask 077
tar -czf "backups/tg-dashboard-$(date +%Y%m%d-%H%M%S).tar.gz" data
```

备份包含加密密钥、会话凭证和任务信息，不要上传至公共仓库。确认新 GHCR 镜像**已成功发布**后，更新相同目录内的 Compose：

```bash
docker compose config --quiet
docker compose pull dashboard
docker compose up -d --wait dashboard
docker compose ps
docker compose logs --tail=100 dashboard
```

首次登录采用原 Dashboard 已有密码，不要求重新配置 `TG_SIGNER_GUI_AUTHCODE`；`ADMIN_PASSWORD` 仅用于空数据库的首次初始化。

宝塔 HTTPS 反向代理环境应将 `COOKIE_SECURE` 改为 `true`；默认容器映射为 `127.0.0.1:8999:8999`，由宝塔代理至该地址。**禁止同时启动两套 Dashboard 写入同一个 SQLite 或使用同一 Telegram Session。**

## 回滚

如发生问题，先停止新容器，将 Compose 中的镜像改回之前已验证的版本，再启动。若升级过程修改过数据库结构，先从停机备份恢复**整个** `data/`（包括密钥、WAL/SHM 文件和 `upstream/`），不要只恢复单个 SQLite 文件。

本次 P0 不修改数据库模型、账号登录或任务配置。下一阶段通用转发引擎需要在隔离数据副本中确认兼容性；此阶段不包含该功能。

## P8 加密备份、迁移与 ARM64

参见 [P8 开发及回滚手册](P8-MAINTENANCE-RELEASE.md)。支持经管理员验证的加密备份上传/下载与冷启动恢复；每次升级前仍建议停机执行宿主机完整 `data/` 目录备份。
