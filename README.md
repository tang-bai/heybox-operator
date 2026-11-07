# 小黑盒运营 · MaiBot 插件

[![release](https://img.shields.io/github/v/release/tang-bai/heybox-operator?label=release)](https://github.com/tang-bai/heybox-operator/releases/latest)
[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)
![tests](https://img.shields.io/badge/tests-93%20passing-brightgreen)

让 MaiBot 用自己的人设在小黑盒逛帖、点赞、评论、发帖和回私信：扫码或 Cookie 登录、推荐流浏览、低额度自动运营、私信自动回复、运维命令和 WebUI 仪表盘。

> 本仓库是 [HannJia/xiaoheihe-operator](https://github.com/HannJia/xiaoheihe-operator) 的**二次开发维护版**（当前 0.8.0，上游 0.5.0）。上游 MIT License，版权与第三方来源见 [LICENSE](LICENSE)、[REFERENCES.md](REFERENCES.md)、[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 功能

- **登录**：WebUI 扫码，或直接粘贴 Cookie（无浏览器/无扫码环境时用）。
- **跟随宿主人设**：评论、回复、发帖默认使用 MaiBot 主配置的昵称与人格，可在插件里单独覆盖。
- **逛推荐流**：按社区白名单挑 2–4 篇点开，点赞限额、偶发评论，按当天剩余额度摊到各场次。
- **自动发帖**：社区 + 话题标签分开选，可选配图（直连生图 API，失败自动降级纯文字）。
- **评论回复**：@ 提及、评论、楼中楼，先读原帖再生成；发送后回读核验。
- **私信自动回复**：好友会话读取 + 白/黑名单范围 + 可选配图；默认关闭、默认 `dry_run`。
- **表情规范化**：`[cube_狗头]` 之类自动映射成平台标准标识符，避免整条评论被判非法。
- **运维命令**：立即检查、重试失败、取消队列、发帖预览。
- **WebUI 仪表盘**：账号、今日额度、队列分布、私信状态、最近 20 条动作，一键检查/重试。
- **稳定性**：每日额度、同人冷却、内容边界、动作幂等、发送结果未知不重发、限制/认证自动暂停。

## 快速开始

1. 把插件目录放进 MaiBot 的 `plugins/`，完整重启 MaiBot。
2. WebUI 打开「小黑盒运营」工作区 → **账号登录** 扫码；或在插件配置里填「手动 Cookie」。
3. 先保持 `dry_run`：把每日发帖设为 1、选一个发帖社区，观察日志出现 `dry_run 已记录草稿`。
4. 切 `auto` 后确认日志出现 `verified`，并能在小黑盒看到对应内容。
5. 私信要先打开「启用私信功能」、模式选 `dry_run`、白名单填 1 个测试 UID。

> 默认 `enabled=true`、`mode=auto`：扫码后就会按额度点赞（3/天）和回通知（1/天）。想绝对安静请先改 `dry_run`。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/xhh_status` | 查看插件、账号、私信与配额状态 |
| `/xhh_check` | 立即执行一轮通知 / 私信 / 巡帖检查 |
| `/xhh_retry` | 把失败的评论回复放回重试队列 |
| `/xhh_clear_queue [帖子ID]` | 取消尚未发送的评论回复（可按帖子过滤） |
| `/xhh_test 帖子ID` | 读帖并生成一条预览回复，不发送 |
| `/xhh_login`、`/xhh_login_check` | 生成扫码二维码 / 检查登录状态 |
| `/xhh_help` | 命令列表 |

## WebUI

插件工作区包含两个页面：

- **账号登录**：生成二维码、轮询扫码状态，不显示原始登录链接。
- **运营仪表盘**：账号与模式、今日额度（发帖/评论/点赞/通知回复/私信）、动作队列分布、私信状态、最近 20 条动作；支持「立即检查通知/私信」和「重试失败」。

仪表盘只返回短字段（内容预览截断 60 字），不暴露完整正文与 Cookie。

## 配置要点

| 配置 | 说明 |
| --- | --- |
| 运行模式 `mode` | `dry_run` 只记录，`auto` 才真正写入 |
| 每日额度 | 发帖 1 / 评论 1 / 点赞 3 / 通知回复 1，可按账号活跃度调整 |
| 时间窗口 | 浏览与发帖各自独立，支持跨午夜；相同起止表示全天 |
| 社区白名单 | 决定逛帖与互动范围，最多 10 个 |
| 发帖社区 / 话题标签 | 社区是真实版块，话题是 `#标签`，可自定义 |
| 生图 API | OpenAI 兼容 `/images/generations`；留空尝试读 MaiBot「画图」provider |
| 生活轨迹 | 读「生活频率」的 `life_state.json`，睡觉时不逛不发 |
| 私信 | 开关、`dry_run`/`auto`、白/黑名单范围、每日额度、冷却、配图 |

## 稳定性与安全

- 凭据只写在 MaiBot 数据目录的 `credentials/`；POSIX 下 `chmod 600`，Windows 无额外保护，注意机器权限。
- 日志与错误详情会脱敏；WebUI 不返回 Cookie/Token。
- 写请求超时或响应不明时记为 `send_unknown`，**不自动重发**；重启后只读回核验。
- 命中「禁止私信」暂停 30 分钟，命中 `web_auth`/`name_verify` 暂停 6 小时并提示去 App 认证。
- 小黑盒没有公开稳定的自动化 API，接口随时可能变化，账号也有被风控的可能；建议低额度、先用 `dry_run`。

## 测试

```powershell
cd tests
$env:PYTHONPATH = "<插件目录>;<插件目录>\tests"
python -m unittest discover -s . -p "test_*.py"
```

当前 93 个用例：SQLite 幂等、通知/私信契约、配置迁移、生图安全边界、表情规范化、手动 Cookie、运维命令、WebUI 仪表盘。

## 与上游（0.5.0）的差异

| 变化 | 说明 |
| --- | --- |
| 修复 `link_tag` | 纯文字帖从非法的 `21` 改为 `28`；图文 27、动态 28、文章 11 |
| 发帖社区/话题分离 | 社区下拉 + 话题标签下拉 + 自定义标签 |
| 浏览去游戏锁 | 去掉游戏关键词硬过滤，范围交给社区白名单 |
| 发帖方向自由 | 不再写死「游戏日常」，由人设 + 生活轨迹生成 |
| 直连生图 | 新增 `image_generation.py`，可用 MaiBot 画图 provider 或独立中转站 |
| 生活轨迹改造 | 读「生活频率」的 `life_state.json` |
| 私信回复 | 会话列表 + 单会话 + 发送确认，白/黑名单、配图、限制暂停 |
| 运维与 WebUI | 表情规范化、手动 Cookie、命令族、运营仪表盘 |

## 目录结构

```
plugin.py                 插件主体：配置、轮询、发送、命令、WebUI API
store.py / dm_store.py    SQLite 状态：动作幂等、通知、私信会话
content_generator.py      评论/私信/发帖提示词
context_provider.py       宿主人设 + 生活轨迹 + 身份参考图
image_generation.py       OpenAI 兼容生图（含 SSRF/体积校验）
emoji_catalog.py          表情标识符规范化
vendor_xiaoheihe/         小黑盒客户端（签名、限流、解析、私信）
webui/                    登录页 + 运营仪表盘
tests/                    93 个 stdlib unittest 用例
```

## 许可与致谢

MIT License。`vendor_xiaoheihe/` 基于 `674537331/astrbot_plugin_xiaoheihe_adapter`（MIT）整理，私信契约参考 `Whereis-Alice/astrbot_plugin_xhhrobot`（Apache-2.0，未复制代码）；完整来源见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [REFERENCES.md](REFERENCES.md)。

变更记录见 [CHANGELOG.md](CHANGELOG.md)，维护/回滚说明见 [MAINTENANCE.md](MAINTENANCE.md)。
