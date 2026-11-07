# heybox-operator

MaiBot 小黑盒运营插件：让 MaiBot 用自己的人设在小黑盒逛帖、点赞、评论和发帖，支持扫码登录、推荐流浏览、自动发帖、评论回复和楼中楼回复，可联动生活轨迹和自建生图。

本项目是 [HannJia/xiaoheihe-operator](https://github.com/HannJia/xiaoheihe-operator) 的**二次开发版本**（上游 MIT License，版权声明见 [LICENSE](LICENSE)）。

本仓库（维护版）：<https://github.com/tang-bai/heybox-operator>

## 与上游的主要差异

- **修复 link_tag bug**：纯文字帖原先给 `21`（非法值）会被小黑盒判「错误的动态分区」拒掉，改为 `28`。正确映射：图文=27、动态=28、文章=11。
- **发帖社区/话题分离**：「发帖社区」下拉（真实社区）+「发帖话题标签」下拉（热门 `#标签`），话题支持自定义填写。
- **浏览不再锁死「游戏」**：去掉「游戏关键词」硬过滤，浏览/互动范围改由「社区白名单」决定。
- **自动发帖自由发挥**：不再写死「游戏日常」方向，由人设 + 生活轨迹自由生成内容。
- **配图直连自建生图 API**：新增 `image_generation.py`，直接调 OpenAI 兼容 `/images/generations`，可用 MaiBot 已加载的画图模型或单独填中转站，不依赖「麦麦绘图」插件。
- **生活轨迹联动「生活频率」**：生活频率插件无 `get_current_context` API，改为直接读其 `life_state.json` 状态文件。

## 功能

- **扫码登录**：WebUI 工作区直接生成二维码，或聊天里 `/xhh_login`。
- **跟随宿主人设**：评论、回复、发帖默认用 MaiBot 主配置的昵称和人格。
- **推荐流浏览**：随机 2–4 篇点开，按社区白名单筛选，点赞每天限量。
- **自动发帖**：低额度自动运营，每天最多 1 帖，配图可选（生图失败自动降级纯文字）。
- **评论回复**：支持 @ 提及、评论、楼中楼回复，先读原帖再生成。
- **私信回复（0.6.x 新增）**：读取私信会话，按「白名单 / 黑名单」范围自动回复，可开关配图；默认 `dry_run`，带每日额度、冷却、幂等和限制暂停。详见 [docs/PRIVATE_MESSAGE_OPTIONS.md](docs/PRIVATE_MESSAGE_OPTIONS.md)。
- **表情规范化与手动登录（0.7.0）**：发送前把 `[cube_狗头]` 之类映射成平台标准表情；无法扫码时可直接粘贴 Cookie 登录。
- **运维命令（0.7.0）**：`/xhh_check` 立即检查、`/xhh_retry` 重试失败回复、`/xhh_clear_queue [帖子ID]` 取消队列、`/xhh_test 帖子ID` 预览回复、`/xhh_help` 命令列表。
- **WebUI 仪表盘（0.8.0）**：工作区新增「运营仪表盘」页，看账号/额度/队列/私信状态，并可一键检查通知与私信、重试失败回复。
- **配额/冷却/幂等/读回核验**：防止重复发送和误发。

## 安装

把插件目录放入 MaiBot 的 `plugins/`，完整重启 MaiBot，在 WebUI 启用并配置。首次使用建议先用 `dry_run` 模式验收，确认无误后再切 `auto`。

> **维护版 0.6.1**：修好了二创版「自动发帖必然失败」「旧配置升级失败」「生图下载缺安全校验」等问题，并新增**私信自动回复**（默认关闭、默认 dry_run，白名单/黑名单范围、可配图、带额度与限制暂停）。69 个测试全部通过。改动清单见 [CHANGELOG.md](CHANGELOG.md)，维护说明见 [MAINTENANCE.md](MAINTENANCE.md)。

## 参考项目与许可证

本项目为二次开发，业务逻辑基于上游独立实现；第三方代码来源和参考项目见 [REFERENCES.md](REFERENCES.md) 与 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。使用 MIT License。
