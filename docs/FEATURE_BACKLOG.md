# 参考项目功能盘点与收纳建议

对比对象：[`Whereis-Alice/astrbot_plugin_xhhrobot`](https://github.com/Whereis-Alice/astrbot_plugin_xhhrobot)（AstrBot 插件，30 个 LLM 工具、15 个配置分区、WebUI + 评论洞察）。
本文只列**能搬到 MaiBot 插件里**的部分，并标注成本（S ≤ 2 小时 / M ≈ 半天到 2 天 / L ≥ 3 天或依赖外部能力）。

## 一、参考项目的功能全貌

| 模块 | 参考实现 | 说明 |
|---|---|---|
| 扫码登录 / Cookie 兜底 | `account_service.py` | 二维码 + 手动 Cookie + 退出登录，凭据走插件存储 |
| 通知与回复 | `event_bridge.py`、`event_dispatch.py`、`reply_generation.py` | 提及、回复、自己帖子下的普通评论；进入 AstrBot 标准事件链 |
| 私信 | `dm_store.py`、`xhh_client.send_direct_message_chain` | 好友/陌生人私信、图片链、限制暂停、额度 |
| 自主巡帖 | `auto_browse.py`、`browse_service.py` | 模型选帖 → 决定评论/跳过 → 预览模式、作者冷却、去重 |
| 社区读取工具 | `tools.py`（`xhh_get_feed` / `xhh_search` / `xhh_get_post` / `xhh_get_user_*` / `xhh_get_topics` / `xhh_get_emojis`） | 只读，公开数据 |
| 账号与统计工具 | `xhh_status` / `xhh_get_notifications` / `xhh_get_favorite*` / `xhh_get_remote_drafts` / `xhh_comment_stats` / `xhh_search_comment_archive` | 私密读取，默认仅管理员 |
| 写操作工具 | `xhh_publish_post` / `xhh_create_comment` / `xhh_set_favorite` / `xhh_set_like` / `xhh_set_follow` / `xhh_delete_post` / `xhh_send_direct_message` | 需要“写操作开关”+ 确认词 |
| 本地草稿箱 | `draft_store.py`、`xhh_get_drafts` / `xhh_save_draft` / `xhh_delete_draft` | 与小黑盒服务端草稿分开 |
| 评论归档与统计 | `comment_archive.py`、`state_store.py` | SQLite 归档收到/发出的评论，支持筛选分页 |
| 评论洞察 | `comment_insights.py`、`insight_jobs.py`、`insight_card*.py` | LLM 自动探索/定向分析 + 终端风卡片渲染 |
| 表情规范化 | `emoji_catalog.py`、`emoji_reference.md` | `[cube_狗头]` → `[cube_doge]`，避免发送被拒 |
| 图片处理 | `media.py`、`rich_content.py` | 评论图片、GIF 首帧、原字节上传、HTML content_blocks |
| WebUI | `pages/dashboard/index.html`、`webui_service.py` | 登录、队列、统计、明细、洞察任务 |
| 稳定性 | `reliability` 配置、`state_store.py` | 单帖回复上限、作者冷却、确认词、错误通知、SOCKS5 代理 |

## 二、我们现在的对照

| 能力 | 现状 |
|---|---|
| 扫码登录 + WebUI 登录页 | ✅ |
| 宿主人设 / 生活轨迹 / 身份参考图 | ✅ |
| 提及 + 回复通知的自动回复（含楼中楼、读回核验） | ✅ |
| 自主逛推荐流（点开 2–4 篇、点赞、偶发评论） | ✅ |
| 自动发帖（社区 + 话题标签 + 配图） | ✅ |
| 私信读取 + 白/黑名单自动回复 + 配图 | ✅（0.6.1） |
| SQLite 幂等、配额、冷却、内容边界、send_unknown | ✅ |
| 社区搜索、帖子详情、用户资料/动态、话题、表情读取 | ❌ |
| 收藏 / 关注 / 评论点赞 / 删帖 | ❌ |
| 评论归档与统计、评论洞察、卡片渲染 | ❌（只有 `incoming_events` / `outgoing_actions` 两张流水表，没有查询与统计） |
| 本地草稿箱、WebUI 仪表盘 | ❌（WebUI 只有登录页） |
| 单帖回复上限、作者冷却、帖子去重 | ❌（只有全局额度/冷却） |
| 评论带图、评论图片的视觉上下文 | ❌ |
| 表情规范化 | ❌ |
| 手动 Cookie 兜底登录 | ❌（只能扫码） |
| 陌生人私信入口 | ❌（只读好友会话列表） |

## 三、可收纳清单（按性价比排序）

### A 级：成本 S，建议先做

1. **表情规范化**（`emoji_catalog.py`）：发送前把模型输出的 `[cube_狗头]` 之类映射成平台认可的标识符，否则整条评论会被判非法。我们目前只有 `is_publishable()` 拦截，没有表情映射表。
2. **手动 Cookie 兜底登录**：扫码拿不到二维码/服务器无浏览器时，允许直接贴 Cookie（与现有 `CredentialStore` 复用，只多一层校验和配置项）。
3. **运维命令**：`/xhh检查`（立即跑一轮）、`/xhh重试`（把 FAILED 放回队列）、`/xhh清空队列 [帖子ID]`。我们的 `store.py` 已有状态机，命令层直接读写即可。
4. **发送前预览**：`/xhh测试 帖子ID` —— 读帖 + 生成回复但不发送，调人设比全局 `dry_run` 更轻。
5. **本地草稿箱**：发帖前先落一条草稿（复用 `store.py`），加 `/xhh草稿` 查看/发布；`dry_run` 只能翻日志，体验差很多。
6. **单帖回复上限 + 作者冷却 + 帖子去重**：热门帖被连续回复很容易被风控；我们只有全局 `notification_replies_per_day`。

### B 级：成本 M，值得排期

7. **只读工具集**（MaiBot 的 `@Tool`）：`xhh_search`（帖子/用户/话题）、`xhh_get_post`、`xhh_get_user_profile`、`xhh_get_user_activity`、`xhh_get_topics`、`xhh_get_emojis`。需要给 `vendor_xiaoheihe` 补 4–6 个端点（search / user / topic index 已有部分）。
8. **收藏 / 关注 / 评论点赞 / 删帖**：写操作，建议做成“默认关闭 + 每次确认”的独立开关，避免和自动运营混在一起。
9. **WebUI 仪表盘**：把 `/xhh_status` 的内容做成页面（队列、最近 20 条动作、配额剩余、私信暂停状态），`webui.json` 加一个 `dashboard` 页即可。
10. **评论归档 + 统计**：`dm_store` 的思路复制一张 `comments` 表，收到/发出的评论都落库，再加一个查询命令；这是“评论洞察”的前置。
11. **陌生人私信入口**：我们已有 `DIRECT_MESSAGE_HISTORY`（`/chat/stranger_messages/`）端点但没接入；加一条来源和开关即可。
12. **评论带图**：参考的 `xhh_create_comment` 支持 `image_urls`，我们的 `send_comment` 固定不带图；要的话把 `imgs` 字段补进请求契约。

### C 级：成本 L 或依赖外部能力

13. **评论洞察 + 卡片渲染**：需要 LLM 任务队列 + 图片渲染管线（参考项目为此写了 `insight_card_template.py` 53KB）。价值高但投入大，且 MaiBot 侧没有现成的图片渲染框架。
14. **视觉上下文**：把评论/原帖图片交给模型。取决于 MaiBot `ctx.llm.generate` 是否支持图片输入，需先验证 SDK。
15. **富文本 content_blocks（HTML 块）**：我们的 `create_post` 已支持 blocks，插件侧只产 `text`/`img`；开放给工具后要加 HTML 白名单校验。
16. **SOCKS5 代理**：我们用的是自带的 urllib 兼容层，代理支持要改传输层，MaiBot 部署通常不需要。

## 四、不建议搬的部分

- **AstrBot 标准事件桥（`event_bridge.py`）**：它把评论/私信塞进 AstrBot 消息链复用 provider/persona/会话历史；MaiBot 的插件模型是直接调 `ctx.llm`，照搬会重复造一套事件系统。
- **UMO 通知、管理员权限、provider/persona 选择**：都是 AstrBot 平台能力，MaiBot 有等价物（`ctx.config`、WebUI 配置），不需要移植。
- **30 个工具全量照搬**：工具越多，模型误调用概率越高；先做 5–6 个高频只读工具足够。

## 五、建议的落地顺序

| 周次 | 内容 |
|---|---|
| 第 1 周 | A 级 1–4：表情规范化、Cookie 兜底、三个运维命令、发送前预览 |
| 第 2 周 | A 级 5–6 + B 级 9：草稿箱、单帖/作者限流、WebUI 仪表盘 |
| 第 3 周 | B 级 7：只读工具集（搜索/帖子/用户/话题/表情） |
| 之后 | B 级 8/10/11/12，视账号风控情况再评估 C 级 |

## 六、参考项目里值得直接照搬的“保护机制”

- 写操作确认词 + 独立写开关（我们的写操作目前只有配置级 `auto`）；
- 单个自己帖子回复上限 + 作者冷却 + 帖子去重；
- 只读接口有限重试、写接口单次提交（我们已经做到）；
- 发送结果不确定时不再重发（我们已经做到）；
- 私信限制暂停（我们已经做到）；
- 错误通知到固定会话（MaiBot 侧需要确认能否主动发消息）。
