# 通用 Telegram 消息监控与转发（P1）

此版本继续使用原 [dongbo501/TG-SIGNER](https://github.com/dongbo501/TG-SIGNER) 的单容器 Dashboard，不更换账号登录、签到或主数据库模型。新增 `relay_message` 通用 Handler 和中文可视化规则编辑器，**原来的 Monitor、forward、external_forward 动作均仍然可用**。

## 开始使用

1. 打开 Dashboard，进入左侧 **自动化与转发**，点击 **新建自动化**。
2. 选择已经登录的 Telegram 账号；点击 **通用消息转发** 模板。
3. 在「什么时候触发 → 收到消息时」填写一个或多个 **来源频道、群组、用户或 Bot**：`@source_a, @source_b, -100123456789`。数字聊天 ID 会在表单中转换为整数；**来源必填**，防止转发器意外监听账号的全部对话。
4. 在「消息筛选」选择 **全部 / 包含 / 精确 / 正则**。可展开「测试消息匹配」粘贴样本，检查捕获组；预览通过 Dashboard 管理员鉴权。
5. 在「匹配后执行」选择 **通用多目标消息转发**，添加多个目标（最多 25 个），每个目标独立配置转发模式、去重策略、失败冷却时间。
6. 保存并启用任务，即可运行持续监听。**不是 Cron 定时触发**；收到消息时运行，支持 Telegram 编辑消息再触发，但默认去重会阻止相同来源消息重复投递。

## 转发目标模式

| 模式 | 行为 | 典型用途 |
| --- | --- | --- |
| `forward` | Telegram 原生转发，显示来源 | 归档频道消息、图片、视频、文件 |
| `copy` | 复制消息，不显示原转发标识 | 内容同步、带媒体复制 |
| `text` | 只发送文本或媒体说明（caption） | 将消息文本投递 Bot |
| `extract` | 用正则提取捕获组，作为文本发送 | 从消息中提取分享链接、验证码 |
| `template` | 将 `{text}`、`{chat_id}`、`{message_id}`、`{sender_id}` 等变量写入文本模板 | 统一通知格式 |

`extract` 支持选定命名或数字捕获组，一条消息最多提取 20 个结果。正则匹配设置执行时间预算，避免无限匹配。纯媒体建议用 `forward` 或 `copy`。

## 去重与失败冷却

| 策略 | 行为 |
| --- | --- |
| `message` | 按规则、来源对话和消息 ID 去重。适合日常转发，重复收到/编辑同一消息不重复发送 |
| `content` | 按目标、模式和发送文本哈希去重，可阻止不同来源频道重复提供相同链接；**转发媒体时按消息身份处理**，避免不同图片使用相同 caption 时误判 |
| `off` | 每次匹配都重新发送，不写去重数据库 |

去重记录保存于 **`./data/upstream/message_relay.sqlite3`**，随原 `./data:/data` 绑定挂载保留；每个成功投递、失败投递均有状态，失败状态可在后续**再次遇到相同来源消息**并超过冷却时间后重试。

**当前没有后台定时扫描失败队列**，失败状态与重试管理面板属于下一阶段 P2。Telegram API 接受发送 ≠ 外部 Bot/服务处理成功；请通过对应 Bot 自身的回执确认结果。

### 示例：115 分享链接交给已有 Bot

在自动化新任务中选择 **115 链接转存 Bot 示例**。把 `@SourceChannel` 替换为真实来源，把 `@Your115SaveBot` 替换为目标 Bot。示例模板匹配 **20xx 年「长时｜连载｜国漫」** 和 `115cdn.com/s/` 分享链接，提取 URL 后向目标 Bot 发送纯文本，并按内容去重。

这仅是消息投递，**不会直接操作 115 网盘账号或自行完成转存**。先通过页面正则测试，再拿一条测试消息验证目标 Bot 的接收和行为。

## 保护旧数据与升级

数据库 `dashboard.sqlite3` 中原有的账号、密码、Session 和任务都不会被转换或删除；不再使用旧 NiceGUI 的 `TG_SIGNER_GUI_AUTHCODE`。

在**原来的部署目录**执行：

```bash
docker compose stop dashboard
mkdir -p backups
chmod 700 backups
umask 077
tar -czf "backups/tg-dashboard-before-relay-$(date +%Y%m%d-%H%M%S).tar.gz" data

# 确认自己的 GHCR 新镜像已经发布且可拉取后：
docker compose pull dashboard
docker compose up -d --wait dashboard
docker compose ps
docker compose logs --tail=100 dashboard
```

备份中必须保留 `dashboard.sqlite3`、`encryption.key`、`jwt.key`、`upstream/`。不要将数据目录、密钥、Telegram Session 或备份包上传到 GitHub。

如果升级后任务有异常，立即停止新 Dashboard，换回已知可用的固定 SHA 镜像；若数据库发生变化，以停机备份恢复**完整** `data/`。不能让两个 Dashboard 同时使用相同数据库或 Telegram Session。

## 已验证的范围和限制

CI 检查包括 Dashboard 原功能回归、继承的 tg-signer 测试、通用转发 Handler 的模拟 Telegram API 发送、原 Dashboard 认证后的正则预览，以及 Docker 构建/启动。**没有使用生产 Telegram 账号实际发送测试消息**。正式使用前，先用测试频道和测试 Bot 进行端到端验证。公开频道/对话用户名和真实数字 ID 需要帐号具备读取/发送权限。
