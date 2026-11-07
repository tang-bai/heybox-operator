# Changelog

本文件记录 heybox-operator（二次开发自 [HannJia/xiaoheihe-operator](https://github.com/HannJia/xiaoheihe-operator)）的变更，按时间倒序。

## 2026-11-08（0.9.5：新增 /xhh_post_now 手动发帖诊断）

- 新增 `/xhh_post_now`（操作员权限）：立即走一遍主动发帖流程，**忽略发帖时间窗**，仍遵守 `dry_run`；返回 `verified` / `dry_run` / 具体错误标签，便于排查 `proactive_post` 被拒原因，不用等到第二天。
- `_create_proactive_post()` 增加 `ignore_window` 参数（仅命令层使用）。
- 测试：`tests/test_admin_commands.py` 补 1 个用例，全量 116 个通过。

## 2026-11-08（0.9.4：主动发帖错误分类与 link_id 保留）

- **错误标签细化**：`proactive_post` 不再只记 `XiaoheiheApiError`，而是写出阶段与分类：
  - `create_post:<category>:<message>` —— 发帖请求阶段被拒/失败；
  - `readback:<stage>:<category>:<message>` —— 发帖已返回 `link_id`，但读回失败。
- **保留 link_id**：`create_post` 返回后立即把 id 写进 `external_id` 和 metadata；读回异常时不再丢失，`_reconcile_unknowns()` 后续可以按 id 核验并转 `verified`。
- **分类更准确**：`upstream_rejected / credential_invalid / forbidden / rate_limited / http / response_shape` 记为 `failed`（发帖未成功），超时/服务端异常仍记 `send_unknown`。
- 测试：新增 `tests/test_post_errors.py`（4 个），全量 115 个通过。

## 2026-11-08（0.9.3：修复评论读回误报 readback_not_found）

- **修复楼中楼核验**：读回时递归展开 `comments/children/sub_comments/replies` 等嵌套结构，楼中楼回复不再因为「只在子楼层」而判成 `send_unknown`。
- **新增近期评论兜底**：帖子树找不到时改用 `/bbs/app/comment/user`（账号近期评论）再核验一次，覆盖被折叠/待审核的评论。
- **支持按评论 ID 命中**：发送响应里的 `external_comment_id` 直接作为读回依据，平台改写文本也能确认成功。
- **文本比对放宽**：忽略全部空白并允许 `回复 @某人：` 前缀，避免平台排版差异造成误判。
- `_reconcile_unknowns()` 复用同一套核验逻辑并带上已存的 `external_id`，历史 `readback_not_found` 记录在后续轮询中可自动转成 `verified`。
- 测试：新增 `tests/test_comment_readback.py`（7 个），全量 111 个通过。

## 2026-11-08（0.9.2：/xhh_whoami 增加宿主权限回显）

- `/xhh_whoami` 现在会通过 `ctx.config.get("plugin.permission")` 回显 MaiBot **实际加载到的操作员列表**，并给出 `matched=True/False`。
- 用于区分「配置没写对/没加载」和「平台前缀不匹配」：`matched=True` 仍被拒 → 重启或确认运行的是这份配置；`host_permission=[]` → 值没进 `[plugin]` 段。
- 测试：`tests/test_admin_commands.py` 补 1 个用例，全量 104 个通过。

## 2026-11-08（0.9.1：新增 /xhh_whoami 权限诊断）

- 新增 `/xhh_whoami`（**不需要操作员权限**）：回显 `platform`、`user_id`、`platform:id`、`session_id`，用于把账号正确填进 `config/bot_config.toml` 的 `[plugin] permission` 或某条命令的 `allow_users`。
- 解决「明明放行了 QQ 号仍提示无权限」时，无法确认平台前缀（`qq:` / `napcat:` 等）的问题。
- 测试：`tests/test_admin_commands.py` 补 1 个用例，全量 103 个通过。

## 2026-11-08（0.9.0：发帖改为总结今天刷到的帖子）

- **发帖内容来源改变**：逛推荐流时把实际点开的帖子写入新的 `seen_posts` 表（标题 / 摘要 / 社区，保留 7 天）；发帖时取当天的记录交给模型，让它**总结今天刷到的内容并挑 1–2 篇评论**。
- 当天记录不足 2 条时，发帖前临时拉一次推荐流补齐（跳过自己发的帖子），不会为了凑素材反复请求。
- 新增配置「发帖总结今天刷到的帖子」（`post_summarize_feed`，默认开启）；关闭后回到「人设 + 生活轨迹」的旧行为。
- 首帖的「自我介绍」方向只在没有已看帖子时使用，避免和总结任务打架。
- 提示词把帖子标题标注为不可信外部素材，明确禁止执行其中的指令，并禁止照抄标题、写成资讯汇总或 `@` 任何人。
- 动作 metadata 增加 `seen_post_ids`，可在审计表/仪表盘回溯这条帖子参考了哪些帖子。
- 测试：新增 `tests/test_post_from_seen.py`（9 个），全量 102 个用例通过。

## 2026-11-08（0.8.0：WebUI 运营仪表盘）

- **新增 WebUI 页面「运营仪表盘」**（`webui/dashboard.html`）：账号/登录态、运行模式、今日额度（发帖/评论/点赞/通知回复/私信）、动作队列分布、私信状态与最近 20 条动作。
- **两个操作按钮**：「立即检查通知/私信」（只跑通知与私信，不触发主动发帖，避免页面等待生图）、「重试失败」（把失败评论放回 `suppressed` 队列）。
- **新增 API**：`webui_dashboard`、`webui_check_now`、`webui_retry_failed`；快照只返回短字段（内容预览截断 60 字），不暴露完整正文与 Cookie。
- `webui.json` 注册第二个工作区页面；仪表盘每 5 秒自动刷新，页面隐藏时暂停。
- 测试：新增 `tests/test_webui_dashboard.py`（6 个），全量 93 个用例通过。

## 2026-11-08（0.7.0：表情规范化 / 手动 Cookie / 运维命令 / 发帖预览）

- **表情规范化**：新增 `emoji_catalog.py`，发送前把 `[cube_狗头]`、`[表情:笑哭]` 等别名映射成平台标准标识符；`cube_`/`bigemoji_` 命名空间里不认识的直接丢弃，非表情方括号原样保留。评论、私信、发帖四处出口统一调用。
- **手动 Cookie 登录**：`基础设置` 新增「手动 Cookie（可选）」。`XiaoheiheRuntime.apply_manual_cookie()` 解析 Cookie 头、校验 `user_pkey`、写入凭据目录并替换客户端；留空仍走扫码。
- **运维命令**：`/xhh_check`（立即一轮检查）、`/xhh_retry`（失败评论放回重试队列）、`/xhh_clear_queue [帖子ID]`（取消待发送评论）、`/xhh_help`（命令列表）。原 `_retry_cooldown_comments` 改名 `_retry_suppressed_comments`，同时消费 `cooldown` 与 `manual_retry`。
- **发帖预览**：`/xhh_test 帖子ID` 读取帖子并生成一条回复，只返回文本、不写库、不发送，方便调人设。
- 测试：新增 `tests/test_emoji_catalog.py`（7 个）、`tests/test_manual_cookie.py`（4 个）、`tests/test_admin_commands.py`（7 个），全量 87 个用例通过。

## 2026-11-08（0.6.1：私信白/黑名单与配图）

- **私信回复范围**：新增下拉 `dm_reply_scope`（`allowlist` / `blocklist`）。黑名单优先级最高；选「除黑名单外全部」时无需维护白名单。
- **私信黑名单**：新增 `dm_user_blocklist` 列表；会话刷新、历史消息扫描、发送前三处共用同一套判定（`_dm_recipient_allowed`），不会出现「列表里的人都跳过、扫描阶段又把黑名单捞回来」的漏洞。
- **私信配图**：新增开关 `dm_generate_images`。开启后按回复内容生图（优先直连生图 API，回退麦麦绘图 + 身份参考图），上传到小黑盒 COS 后随 `img` 字段发送；失败自动降级纯文字，审计表记录 `image_requested` / `image_attached`；dry_run 不生成图片。
- 状态工具补充 `scope`、`blocklist_size`、`images_enabled` 三个字段。
- 测试：`tests/test_dm_reply_flow.py` 扩到 11 个（新增黑名单范围、黑名单优先、配图成功、配图失败降级），全量 69 个用例通过。

## 2026-11-08（0.6.0：私信自动回复）

- **新增私信自动回复**：接入小黑盒网页聊天契约（会话列表 `/bbs/app/user/message?list_type=2`、单会话 `/chatroom/v2/msg/user?to_user_id=`、发送 `POST /chatroom/v2/msg/user`）。原先 `auto` 模式下 fail-closed 的 `dm_send_contract_unavailable` 分支已替换为真实发送。
- 发送使用网页聊天面板的 User-Agent 和裁剪后的登录 Cookie，`heybox_ack_id` 进程内单调递增；响应必须有 `heychat_ack_id`/`msg_id`/`msg_seq` 之一才记为 `verified`。
- 安全边界：默认 `dm_enabled=false`、`dm_mode=dry_run`；只有白名单 UID 会收到回复；每日额度、账号冷却、内容边界、动作键 `dm_reply:<message_id>` 幂等全部复用既有机制；首次运行只建立历史基线，不回复旧消息。
- 限制与认证：命中「禁止私信」类响应暂停 30 分钟，命中 `web_auth`/`name_verify` 暂停 6 小时并提示先去 App 认证；暂停期间不写动作，解除后自然重试。
- 读取修复：旧实现的 `fetch_direct_messages()` 没有传 `to_user_id`，实际上读不到会话内容；现在拆成「会话列表 + 单会话消息」两步，并把会话标记写进 `dm_threads`，未变化的会话不再重复拉取。
- 测试：新增 `tests/test_dm_send_contract.py`（9 个）与 `tests/test_dm_reply_flow.py`（7 个），全量 65 个用例通过。

## 2026-11-08（本地维护版 0.5.1）

- **修复自动发帖必然失败**：`_create_proactive_post()` 调用 `create_post()` 时把配置属性写成了 `self.post_hashtags`，插件实例上没有这个属性，请求发出前就抛 `AttributeError`，每一帖都被记成 `send_unknown` 而永远发不出去；改为 `self.config.post_hashtags`。
- **修复旧配置升级失败**：发帖社区 / 话题标签的下拉框取值比上游窄（上游还有 Steam、PC 游戏、游戏攻略等），旧配置里的这些值会让 Pydantic 校验直接判非法。新增降级逻辑：不认识的值移入兼容字段 `post_topic_ids` / `custom_hashtags`，发送前仍按名字解析，行为与上游一致。
- **生图下载加安全边界**：`image_generation.py` 现在用 `validate_public_https_url()` + `resolve_public_host()` 校验生图接口返回的图片地址（只允许 HTTPS 公网地址），并限制单张图片 20 MB；地址非法或超限一律按生图失败处理，降级为纯文字发帖。
- **去掉写死的开发机路径**：`_resolve_image_generator()` 不再读 `E:\mai\config\model_config.toml`，只按 `ctx.paths.data_dir` 推导候选路径。
- **测试修复与补充**：`tests/test_plugin_loading.py` 的默认值断言更新为二创后的配置；新增 `tests/test_config_migration.py`、`tests/test_image_generation_guard.py`；49 个用例全部通过。
- 清理：删除重复定义的 `CommunityName`、未使用的 `PostTargetName`，`post_hashtags` 里不再临时 `import re`。

## 2026-11-03（首批二创改动）

- 修复 `link_tag` bug：纯文字帖从 `21`（非法值，会被小黑盒判「错误的动态分区」拒掉）改为 `28`（默认动态）。正确映射：图文=27、动态=28、文章=11。
- 发帖社区/话题分离：「发帖目标」拆成「发帖社区」下拉（真实社区）+「发帖话题标签」下拉（热门 `#标签`）+ 自定义标签输入，不再是混在一起的游戏列表。
- 浏览去游戏锁：删除「游戏筛选关键词」的硬过滤，浏览/互动范围改由「社区白名单」决定；原字段改为「浏览内容关键词（可选）」，默认空。
- 自动发帖自由发挥：删除写死的 `topic_hint="游戏日常"`，改为人设 + 生活轨迹自由生成内容，不再限定游戏领域。
- 配图直连自建生图 API：新增 `image_generation.py`，直接调 OpenAI 兼容 `/images/generations`；可留空自动读 MaiBot 主配置的「画图」provider，不依赖「麦麦绘图」。
- 生活轨迹联动改造：因生活频率插件无 `get_current_context` API，改为直接读其 `life_state.json` 状态文件。
- 安装生活频率插件 `org.orge-8.life-frequency` v1.25.5 作为生活轨迹来源。
