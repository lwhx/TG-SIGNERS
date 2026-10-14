# P7 转发能力拓展：Bot 回执、媒体组与历史补抓

在 P4/P5 投递账本与 P6 配置版本上增量开发，不改动既有账号 Session、任务或目录挂载。

## 115 Bot 结果追踪（严格区分“送达”和“转存”）

在自动化任务编辑器 → 通用转发目标开启「追踪目标 Bot 的处理结果」。成功正则与失败正则必填且最多 500 字符，回复时限 1–10080 分钟，默认 120 分钟。仅 Telegram 确认送达且结果携带接收会话 Chat ID 时登记 `pending`。回执由 Bot 的实际 Telegram 账号发送，且必须在 **同一账号、同一 Bot 对话、reply_to_message.id 等于已发送 message_id** 三个条件同时成立才可关联；不通过接收时间、发送顺序或 115 链接内容猜测。

- `succeeded` / `failed`：回复命中成功/失败正则（失败优先），**不修改 Telegram 的 `sent` 状态**，也绝不据此自动重发
- `unclassified`：收到明确关联的 Bot 回复，但未命中任何正则；后续关联回复仍可更新
- `pending` / `expired`：未收到明确匹配结果；超时不代表转存失败
- 持久化：`message_relay.sqlite3` 新增 `relay_bot_results`，只保存目标/消息 ID、状态、回复 ID、回复 SHA-256，**不保存消息正文或链接**。随其投递历史清理一起删除。
- 查询：`GET /api/relay/bot-results`；结果表与 Telegram 投递历史分开，需已登录管理员。
- 局限：不回复原消息的 Bot 无法自动确认，用户需核对 Bot 聊天。只监控当前已启用自动化任务绑定的 Telegram 客户端所收到的消息。

## 媒体组原子投递（相册）

目标为 `forward` 或 `copy` 时可以选择 `album_mode=album`，默认 `single` 以保持旧版行为。读取完整的 2–10 张媒体后，使用 `forward_messages([...ids])` 或 `copy_media_group(...)` 一次 RPC 发送完整相册；单张消息照旧转发。来源不完整/不可读时停止整组预检，不退化为发送单张。

- 整组采用同一指纹，包含账号/任务/规则/来源/媒体组 ID/目标/模式。**即使普通去重设置为 off，相册仍永久按组去重**。
- 账本新增 `media_count`、`media_group_key`；投递类型 `album_forward/album_copy`，在转发历史可辨识。部分或不可证实结果进入 `uncertain`，禁止自动和人工直接重试，先人工核对实际目标聊天。
- 状态查询：`GET /api/relay/media-groups/stats`。
- 存在 Telegram 客户端/账号对转发、转发受限内容、媒体类型的原生限制，测试模拟不代表所有聊天都允许整组复制。

## 历史消息补抓

「转发增强」页面先选已启用的自动化任务与规则中**明确声明**的来源；每批 1–50 条，可以用 `before_id` 指定早于某条 Telegram 消息 ID。操作为：预检配置 → 查看范围 → 人工确认启动 → 查看批次进度 → 完成本批后可填入“继续更早历史”的游标。补抓按 **旧→新** 顺序处理，一批完成后不自动启动下一批。

- 只执行 `relay_message`，禁止其他 Handler（如 AI、签到、HTTP、自定义脚本），且拒绝 `dedup=off` 的目标以及忽略自身消息关闭的规则；强制复用 P4 历史去重和不确定投递保护。
- 不会读取不在已启用规则的明确消息触发器中的聊天来源；配置/绑定账号或任务状态变化会在**每次发送前再次检查**。
- 补抓每条消息间等待 1 秒，整批最多执行 10 分钟，单服务进程只允许运行一个批次，避免洪泛 Telegram API；请按 Telegram 的实际限流表现适当减小条数。
- 数据库 `dashboard.sqlite3` 增加 `relay_backfills` 任务状态表，只记录来源、游标、处理条数、状态与错误类型，不记录消息正文。
- 服务重启或中途取消标记为 `interrupted`，**不自动恢复**。人工再次运行时应使用原批次的 `before_id`，依赖 P4 的投递指纹防止已发送内容重复发送；不得用已处理消息 ID 盲目跳过剩余消息。
- `POST /api/relay/backfill/preview`：只校验规则，不连接 Telegram、不读消息。
- `POST /api/relay/backfill/start`：需要 `confirmation=BACKFILL`，后台执行。
- `GET /api/relay/backfill/jobs`：查询最近批次。
- `POST /api/relay/backfill/jobs/{id}/cancel`：停止尚未结束的批次。

## 上线与回滚

升级前务必完整备份 `data/`，尤其 `dashboard.sqlite3`、`message_relay.sqlite3`（包含 WAL/SHM）与密钥。P7 仅添加 SQLite 表与列。升级后若降回旧镜像，建议同时恢复升级前完整备份；避免旧版本与新版本数据格式混用。

自动化测试不使用真实 Telegram 凭据，通过受控假客户端验证相册成组去重、Bot 回复精确关联、补抓权限和停止/中断。实际 115 Bot 是否使用 reply_to_message 以及使用哪些成功/失败语句必须在用户自己的 Bot 聊天中验证；未确认回复格式时不可自动标记为转存成功。
