# 浏览总结发帖（0.10.0）

按「浏览过的帖子」攒素材，到量/到时后写一篇 200–400 字、2–4 段的总结帖。设计参考 `SengokuCola/xiaoheihe_adapter` 的 digest 流程。

## 流程

1. **累计素材**：逛推荐流点开一篇帖子时写入 `digest_reads`（`post_id` 主键，天然去重）；同时记录该帖下自己**已成功发送**的评论（`outgoing_actions` 里 `status=verified`）。
2. **触发**：`summary_trigger` 支持：
   - `count`：待总结帖数 ≥ `summary_every_posts`（默认 10）；
   - `interval`：距上一批开始 ≥ `summary_interval_hours`（默认 24）；
   - `either`：两者先到先发。
   有未完成的批次时不会开新批次。
3. **生成**：`content_generator.digest()` 取最多 100 帖、约 2400 字素材，要求输出 `{"title","paragraphs"}`，正文 200–400 字、2–4 段；优先走宿主 Replyer（人格/记忆/Hook），失败回退插件模型。
4. **发布**：每段作为独立 `text` block 发到「发帖社区」；读回核对标题与段落拼接后的正文。
5. **状态**：`generating → ready → sending → sent`；失败分 `error`（生成/配置问题）、`failed`（平台明确拒绝）、`unknown`（发送结果不明）。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/xhh_summary` | 查看待总结帖数与最近一次状态 |
| `/xhh_summary_now` | 忽略触发条件，立即用当前素材生成一次 |
| `/xhh_summary_retry` | 仅 `error` / `failed` 可重试，复用已保存草稿 |
| `/xhh_summary_skip` | 跳过本轮；素材保留到下一批 |

`unknown` 不允许直接重试：先去小黑盒核对是否已发布，再决定跳过。

## 与按天自动发帖的关系

开启「浏览总结发帖」后，`posts_per_day` 那条按天发帖路径会停止，避免一天出现两篇帖子；发帖时间窗、冷却仍由总结流程自行判断（`/xhh_summary_now` 忽略触发条件）。

## 配置

| 字段 | 默认 | 说明 |
| --- | --- | --- |
| `summary_enabled` | 关闭 | 总开关 |
| `summary_trigger` | `count` | 触发方式 |
| `summary_every_posts` | 10 | 每多少帖总结一次 |
| `summary_interval_hours` | 24 | 间隔多少小时 |
| `post_target_primary` | 空 | 总结帖发布社区（必须选） |

## 失败排查

- `empty_generated_digest`：模型没输出合法 JSON，检查模型/提示词；
- `content_boundary`：标题或段落命中敏感/技术词边界；
- `no_topic`：没有选「发帖社区」；
- `topic_unresolved`：社区名解析不到唯一 topic；
- `failed:upstream_rejected`：平台明确拒绝，按错误信息调整；
- `unknown:readback_not_found`：帖子可能已发布但读回不一致，先核对再跳过。
