# 上游功能覆盖与网页入口

核对来源：上游固定版本 `fc8905db2ee74e2d9afc135cce88b50be35b6212` 的 README、AGENTS、`config.py`、`core.py`、`cli/`、`automation/`、`sign_record_store.py` 以及 `docs/automation_usage.md`。

| 上游 / 请求能力 | 网页入口 | 实现 |
|---|---|---|
| login：验证码、2FA、自定义 API、代理 | 账号 → 添加账号 | 后端异步状态机；会话落库加密 |
| Session String / `.session` 导入 | 账号 → 添加账号 → 导入 Session | Pyrogram/Kurigram；联网验证授权 |
| list / run / run-once / reconfig | 签到任务 | CRUD、启停、Cron、立即执行、取消 |
| export / import（含 YAML） | 任务编辑器与任务操作菜单 | 完整上游配置导出、上传 JSON/YAML、模型校验 |
| 发送文本、骰子、按文本点击按钮 | 任务 → 执行动作 | 可排序动作表单；调用上游执行方法 |
| AI 图片选项、AI 计算题 | 任务 → 执行动作 | 上游 AI 实现；设置页提供 API 配置 |
| 同任务多目标 Chat | 任务 → 添加目标 | 每个目标独立动作、话题、间隔与删除延迟 |
| 正则结果校验 | 任务 → 调度与结果校验 | 普通/编辑消息及按钮回调；失败优先；逐目标确认 |
| 随机误差时间 | 任务 → 随机延迟上下限 | Cron + 随机秒数，采样时间持久化 |
| multi-run | 任务 → 更多 → 多账号执行 | 同配置复制到选择的账号，禁用副本自动调度，立即运行 |
| list-folders / --from-folder | 工具箱 → 文件夹 / 对话；任务 Folder 字段 | 普通 Folder 名称/ID；上游动态 Folder 限制原样保留 |
| list-topics / message_thread_id | 工具箱 → 话题；动作表单话题字段 | 群组话题发现、按话题发送与回复路由 |
| list-members | 工具箱 → 查询成员 | 搜索词、数量、仅管理员 |
| send-text / send-dice | 工具箱 → 发送消息 / 骰子 | 话题、延时删除 |
| schedule-messages | 工具箱 → 批量定时消息 | Telegram 服务器定时发送，Cron、次数、随机秒数、话题 |
| list-schedule-messages | 工具箱 → 查看定时消息 | 同时提供按消息 ID 删除 |
| logout | 工具箱 → 撤销 Session | Telegram 服务端注销并禁用此账号任务 |
| 账号备注、头像、Ping、Refresh、Export、Delete | 账号卡片 | 最近连接状态，每五分钟验证会话；本地删除与服务端注销分开 |
| automation init/list/validate/run/export/import/reconfig | 自动化规则 | 模板、完整 JSON 编辑、JSON Schema、启停、导入导出 |
| message / timer / startup | 自动化规则完整配置 | 原生复用上游触发器，含编辑消息 |
| 全部内置 handlers | 自动化配置 | send_text、reply_text、extract_regex、random_pick、delay、schedule_next、ai_reply、blacklist_filter、forward、external_forward、server_chan、store_state、load_state |
| 通用多来源 → 多目标转发 | 自动化与转发 → 新建自动化 → 通用消息转发模板 | `relay_message` 目标模式 forward/copy/text/extract/template，每个目标独立配置 |
| 正则测试与捕获组 | 自动化与转发 → 消息筛选 → 测试消息匹配 | 管理员鉴权后的 `/api/relay/preview`，超时限制，显示命中与捕获组 |
| 持久化去重与失败冷却 | `data/upstream/message_relay.sqlite3` | message/content/off；失败后冷却，收到重复消息时允许再试 |
| 转发记录和 7 天投递统计 | 左侧「转发记录」 | 分页、状态/账号/任务筛选；基于数据库最终状态统计总量、错误类型、尝试次数 |
| 失败投递人工重试 | 「转发记录」→ 对新版本失败行点击重试 | 账号/任务/规则/来源消息重新核验；原文哈希不符或已成功则拒绝；旧行只能查看 |
| 115 Bot 转存示例 | 自动化与转发 → 115 链接转存 Bot 示例 | 匹配 115cdn.com 分享链接，提取后发送至现有 Bot |
| 自定义 Python handler | 工具箱 → Python 插件 | 文件列表、代码编辑、语法校验、保存、删除；任务重启重新加载 |
| automation 状态持久化 | data/upstream/automations/<id>/state.json | 原生 RuleStateStore，重启恢复 |
| monitor 全部 MatchConfig 字段 | 自动化规则 → 新建消息监控 → JSON 编辑 | 关键词/正则、发送者过滤、模板、AI、转发、Server酱、删除 |
| list-sign-records / migrate-sign-records | 工具箱 → 历史记录迁移 | 上游 SQLite 查询；扫描并迁移旧 JSON，保留原文件 |
| llm-config | 设置 → AI 能力 | 密钥加密；兼容接口地址与模型 |
| WebUI | 整个控制台 | Next.js 现代界面替代 NiceGUI 页面；未在容器重复启动旧 WebUI |
| 概览、7 日趋势、下一次倒计时 | 概览 | 来自真实 SQLite 统计，无演示账号和虚构记录 |
| 实时终端 / 执行历史 | 日志与终端 | JWT Cookie 鉴权 WS、EventSource 回退、有界缓存、重连、搜索、筛选、分页、导出 |
| Bark / Telegram Bot / Server酱 / PushDeer / Webhook | 设置 → 消息通知 | 多渠道失败通知及主动测试按钮 |
| 面板密码 / JWT / Cookie | 登录页 / 设置 → 安全 | Argon2、12 小时 JWT、HttpOnly、SameSite、跨站校验、密码变更撤销旧令牌 |

自动化和旧版监控使用完整 JSON 编辑器覆盖上游全部字段；常用签到提供图形表单。插件具有容器用户的 Python 执行权限，只有面板管理员可以安装。

网页中的任务主数据来自 dashboard SQLite，不会自动接管目录中未导入的旧任务。迁移配置时从旧 CLI 导出 JSON/YAML，在网页新建相应任务并上传。将旧签到记录放入 `data/upstream/signs/`，再使用记录迁移功能。CLI 导出的原生配置不包含面板专有的成功/失败正则、随机延迟下限或账号关联；这些保存在面板数据库并随数据目录备份。

内置执行器基于固定版本且已加入自定义 relay_message 扩展。升级上游时应重跑上游、Dashboard 和通用转发回归测试，不直接替换运行容器中的 Python 包。
