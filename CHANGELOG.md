# Changelog

本文件记录 heybox-operator（二次开发自 [HannJia/xiaoheihe-operator](https://github.com/HannJia/xiaoheihe-operator)）的变更，按时间倒序。

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
