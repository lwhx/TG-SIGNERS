# P6：配置与管理效率

本阶段基于现有 Dashboard、P4 投递安全边界与 P5 故障中心增量开发。默认单容器、单 worker、原 `data/` 挂载保持不变。

## 1. 独立的任务分组
- 新增 `Task.group_name`（最多 60 字符），与原 `folder` **不是同一概念**；`folder` 继续用于 Telegram 对话 Folder 发现。
- 可在新建/编辑任务时设定管理分组，任务页按分组筛选，批量移动分组（空名称表示移出分组）。
- 使用幂等 `ALTER TABLE tasks ADD COLUMN group_name ... DEFAULT ''` 原位迁移；旧任务自动归入未分组，不覆盖 `folder` 和 `config`。

## 2. 复制与批量管理
- `POST /api/tasks/{task_id}/duplicate` 原样复制任务配置到相同或指定账号，新的任务**永远默认停用**，不会因复制立即签到或转发。可自定义名称。
- `POST /api/tasks/batch` 支持 `enable/disable/move_group/delete`；批次 1–50 个任务，服务端要求所有任务 ID 存在才执行，执行前通过 `dry_run=true` 预览；批量删除还要求 `confirmation=DELETE`。网页二次确认，不需要用户手输繁琐口令。
- 禁止空批次、重复 ID 和控制字符。启动/停用/删除重新协调调度状态；仅改分组不重启监听器。保留历史执行记录，删除任务时其配置版本随任务删除。

## 3. 完整转发预览
- 自动化任务编辑器中输入样例来源、发送者、消息 ID、正文和媒体等参数，可模拟来源触发器、筛选条件、多个目标的五种转发模式（forward/copy/text/extract/template），展示每个目标的文本（前 5 条）和理论投递数量。
- 后端使用与实际 Handler 共用的 `_content_for_target` 生成内容，正则时间有界；最多预览 20 条规则、100 条理论发送结果。预览不连接 Telegram、不执行 Handler、不写入去重账本、不保存样例消息。
- **边界**：仅模拟 message 触发、规则初始 vars 和 relay_message；前置 Handler、持久 vars、Telegram 权限、冷却及历史去重结果需通过实际运行验证，外部 115 Bot 转存结果不在模拟范围。

## 4. 配置版本
- 新增 `task_versions`，保存包含账号、组、执行计划、完整规则配置等的任务快照，不包含账号 Session 或 Telegram 登录凭据。
- 既有任务首次升级生成 `baseline` 版本；新建、手动修改、批量状态/分组更改与复制均会产生版本记录。
- 通过 `GET /api/tasks/{id}/versions` 查询最近 100 个版本；`GET /api/tasks/{id}/versions/{version}` 读取完整快照；`POST /api/tasks/{id}/versions/{version}/restore` 带 `expected_version` 乐观冲突保护，重新校验并生成新修订版本。
- **重要**：还原历史配置保留任务当前启用/停用状态，不重新启用已停用任务；保留 P4 的投递账本和去重标记，不会因为回滚直接重复发送旧消息。
- 界面提供并排 JSON 快照与字段差异，确认后才恢复。

## 5. 升级回滚
升级前备份全部 `data/`（包含 SQLite WAL、`encryption.key`、`jwt.key`、`upstream/`）。升级会在现有数据库中增加列/表，不删除旧数据。降级旧镜像之前应同步恢复升级前的完整备份，避免旧版本无法识别新的元数据。

验证通过 Python API/迁移/预览/安全重试回归、Ruff、TypeScript/Next 构建、浏览器回归、Docker 构建与持久数据冒烟测试；实际 Telegram 发送与外部 Bot 转存仍需要用户部署环境验收。

下一阶段：P7（115 Bot 结果追踪、媒体组、历史补抓）。
