from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any, ClassVar, Iterable, Literal, get_args

from maibot_sdk import API, Command, Field, MaiBotPlugin, PluginConfigBase, Tool

try:
    from maibot_sdk.types import ToolParameterInfo, ToolParamType
except ImportError:  # pragma: no cover - local loading stub
    ToolParameterInfo = None  # type: ignore[assignment,misc]
    ToolParamType = None  # type: ignore[assignment,misc]


# 这些是用户能在配置页看到的名称。插件会在发送前通过小黑盒的搜索接口
# 解析为真实 topic_id，避免要求用户手工查找一串数字。
COMMUNITY_OPTIONS = (
    "AI",
    "数码硬件",
    "Steam",
    "校园生活",
    "百科知识",
    "绝地求生",
    "盒友杂谈",
    "沙雕日常",
    "PC游戏",
    "影之刃零",
    "三角洲行动",
    "情投一盒",
    "原神",
    "CS2",
    "英雄联盟",
    "明日方舟",
    "深空当铺：可能是偷的",
    "赛博朋克2077",
    "主机游戏",
    "动漫",
    "小说",
    "职场工作",
    "美食",
    "汽车",
    "绘画",
    "影视",
    "萌宠",
    "体育运动",
    "周边",
    "桌游",
    "美图自拍",
    "Apex 英雄",
    "战争雷霆",
    "逃离塔科夫",
    "无畏契约",
    "守望先锋",
    "星际战甲",
    "绝地潜兵2",
    "街头霸王6",
    "战地风云™ 6",
    "炉石传说",
    "艾尔登法环 黑夜君临",
    "刀塔2",
    "永劫无间",
    "暗区突围：无限（PC）",
    "命运2",
    "幻兽帕鲁",
    "弧光猎人",
    "刀塔综合区",
    "黑神话：悟空",
    "Switch",
    "Gal游戏综合区",
    "艾尔登法环",
    "荒野大镖客：救赎2",
    "边缘世界",
    "怪物猎人：世界",
    "怪物猎人：荒野",
    "剑星",
    "天国：拯救2",
    "杀戮尖塔",
    "空洞骑士：丝之歌",
    "空洞骑士",
    "龙之信条2",
    "光与影：33号远征队",
    "方舟：生存进化",
    "猛兽派对",
    "消逝的光芒2：坚守人性",
    "霍格沃茨之遗",
    "逃离鸭科夫",
    "消逝的光芒：困兽",
    "手机游戏",
    "碧蓝档案",
    "绝区零",
    "崩坏：星穹铁道",
    "王者荣耀",
    "火影忍者",
    "BanG Dream! 少女乐团派对!",
    "洛克王国手游",
    "Fate/Go",
    "碧蓝航线",
    "金铲铲之战",
    "英雄联盟手游",
    "崩坏3",
    "少女前线",
    "重返未来：1999",
    "战双帕弥什",
    "胜利女神：新的希望",
    "使命召唤手游",
    "尘白禁区",
    "穿越火线-枪战王者",
    "荒野乱斗",
    "第五人格",
    "恋与深空",
)
POST_TARGET_OPTIONS = ("",) + COMMUNITY_OPTIONS
DEFAULT_COMMUNITY_ALLOWLIST = ("PC游戏", "盒友杂谈")
MAX_ALLOWLIST_ITEMS = 10
MAX_POST_TARGETS = 2


def _hhmm_to_minutes(value: object) -> int | None:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2:
        return None
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError:
        return None
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour * 60 + minute


def _time_window_active(start: str, end: str, now: float | None = None) -> bool:
    """判断服务器本地时间是否落在 HH:MM 时间窗口内。

    起止时间相同表示全天；结束时间早于开始时间时按跨午夜窗口处理。
    配置格式不合法时安全返回 False，避免意外自动执行。
    """

    start_minutes = _hhmm_to_minutes(start)
    end_minutes = _hhmm_to_minutes(end)
    if start_minutes is None or end_minutes is None:
        return False
    if start_minutes == end_minutes:
        return True
    current = time.localtime(time.time() if now is None else now)
    current_minutes = current.tm_hour * 60 + current.tm_min
    if start_minutes < end_minutes:
        return start_minutes <= current_minutes < end_minutes
    return current_minutes >= start_minutes or current_minutes < end_minutes


def _window_seconds_left(start: str, end: str, now: float | None = None) -> float:
    """当前时间窗口今天还剩多少秒；额度按自然日重置，所以最多算到午夜。

    不在窗口内或配置不合法时返回 0；全天窗口返回距午夜的秒数。
    """

    current_ts = time.time() if now is None else now
    if not _time_window_active(start, end, current_ts):
        return 0.0
    current = time.localtime(current_ts)
    current_seconds = current.tm_hour * 3600 + current.tm_min * 60 + current.tm_sec
    to_midnight = 86400 - current_seconds
    start_minutes = _hhmm_to_minutes(start)
    end_minutes = _hhmm_to_minutes(end)
    if start_minutes is None or end_minutes is None or start_minutes == end_minutes:
        return float(to_midnight)
    end_seconds = end_minutes * 60
    if end_seconds > current_seconds:
        return float(end_seconds - current_seconds)
    # 跨午夜窗口且现在在午夜前：今天的额度只到午夜。
    return float(to_midnight)


# 生活频率 get_current_context 的首行形如「当前活动：睡觉（卧室）」。
# 只认睡觉和小睡；「准备睡觉」时人还醒着，可以刷手机。
_ASLEEP_PATTERN = re.compile(r"当前活动[：:]\s*(?:睡觉|小睡)")

# Literal 让 MaiBot SDK 自动生成真正的下拉框和选项；显示给用户的是中文名称，
# 插件运行时再通过小黑盒搜索接口解析为内部 topic_id。
# 发帖目标分为「社区」和「话题」两类，分别用独立的 Literal 下拉框。
# 数据来自小黑盒 TOPIC_INDEX 接口，用 game.app_id 区分：
#   app_id == -1  => 社区（版块）
#   app_id != -1  => 具体游戏话题
# 两个都是静态 Literal（MaiBot SDK 配置下拉框只支持静态枚举），
# 发送前仍由 resolve_topic_names() 把名字解析成真实 topic_id。
CommunityName = Literal[
    "",
    "AI",
    "Gal游戏综合区",
    "PC游戏",
    "Switch",
    "主机游戏",
    "体育运动",
    "刀塔综合区",
    "动漫",
    "周边",
    "小说",
    "影视",
    "情投一盒",
    "手机游戏",
    "数码硬件",
    "校园生活",
    "桌游",
    "汽车",
    "沙雕日常",
    "百科知识",
    "盒友杂谈",
    "绘画",
    "美图自拍",
    "美食",
    "职场工作",
    "萌宠",
]

HashtagName = Literal[
    "",
    "盒友日常",
    "steam游戏",
    "单机游戏",
    "单机游戏专项创作计划",
    "pc游戏",
    "switch游戏",
    "数码硬件专项创作计划",
    "galgame推荐",
    "战雷日常",
    "2026最期待游戏",
    "碧蓝档案日常分享",
    "独立游戏",
    "艾尔登法环日常分享",
    "杀戮尖塔日常分享",
    "怪物猎人世界日常分享",
    "国产游戏",
    "ai",
    "肉鸽游戏",
    "数码",
    "恐怖游戏",
]

RunMode = Literal["dry_run", "auto"]
DmReplyScope = Literal["allowlist", "blocklist"]
SummaryTrigger = Literal["count", "interval", "either"]

try:
    from .drawpic_gateway import DrawingGateway
    from .image_generation import DirectImageGenerator, ImageGenerationError, build_generator
    from .content_generator import ContentGenerator
    from .context_provider import PersonaContextProvider
    from .dm_store import DirectMessageStore
    from .emoji_catalog import normalize_outgoing_text
    from .host_replyer import HostReplyer
    from .models import IncomingEvent, OutgoingAction, OutgoingStatus
    from .policy import content_fingerprint, is_publishable, normalize_text
    from .qr_generator import qr_data_url
    from .store import OperatorStore
    from .vendor_xiaoheihe.models import Notification, NotificationType, RoutingTarget
    from .xiaoheihe_runtime import XiaoheiheRuntime
except ImportError:  # pragma: no cover - direct Runner fallback
    from drawpic_gateway import DrawingGateway
    from image_generation import DirectImageGenerator, ImageGenerationError, build_generator
    from content_generator import ContentGenerator
    from context_provider import PersonaContextProvider
    from dm_store import DirectMessageStore
    from emoji_catalog import normalize_outgoing_text
    from host_replyer import HostReplyer
    from models import IncomingEvent, OutgoingAction, OutgoingStatus
    from policy import content_fingerprint, is_publishable, normalize_text
    from qr_generator import qr_data_url
    from store import OperatorStore
    from vendor_xiaoheihe.models import Notification, NotificationType, RoutingTarget
    from xiaoheihe_runtime import XiaoheiheRuntime


def _ui_field(*, label: str, hint: str, order: int, **kwargs: Any):
    """统一给 MaiBot 配置页补齐中文标签、提示和顺序。"""

    extra = dict(kwargs.pop("json_schema_extra", {}) or {})
    extra.update({"label": label, "hint": hint, "order": order})
    return Field(json_schema_extra=extra, description=hint, **kwargs)


class XiaoheihePluginSection(PluginConfigBase):
    """插件元信息；旧版扁平配置中的业务字段会自动迁移。"""

    __ui_label__ = "插件信息"
    __ui_icon__ = "settings"
    __ui_order__ = 0

    config_version: str = _ui_field(
        label="配置版本",
        hint="由插件维护，用于识别配置格式；通常不需要手动修改。",
        order=0,
        default="0.1.0",
        json_schema_extra={"disabled": True},
    )


class XiaoheiheBasicSection(PluginConfigBase):
    __ui_label__ = "基础设置"
    __ui_icon__ = "settings-2"
    __ui_order__ = 1

    enabled: bool = _ui_field(
        label="启用自动运营",
        hint="发布版默认开启自动运营；关闭后只保留登录和只读检查，不执行自动发帖、评论或回复。",
        order=0,
        default=True,
    )
    profile_id: str = _ui_field(
        label="小黑盒账号档案 ID",
        hint="当前账号使用的档案名称；单账号保持 default，多账号时再区分。",
        order=1,
        default="default",
    )
    mode: RunMode = _ui_field(
        label="运行模式",
        hint="请选择 dry_run 或 auto；发布版默认 auto。dry_run 只生成并记录计划，auto 才会向小黑盒发送内容。",
        order=2,
        default="auto",
        placeholder="请选择 dry_run 或 auto",
    )
    manual_cookie: str = _ui_field(
        label="手动 Cookie（可选）",
        hint="无法扫码时可直接粘贴小黑盒网页端的 Cookie（形如 user_pkey=...; heybox_id=...）。填写后会优先使用它，留空则继续用扫码登录保存的凭据。",
        order=3,
        default="",
        json_schema_extra={"x-widget": "textarea"},
    )


class XiaoheiheBrowseSection(PluginConfigBase):
    __ui_label__ = "浏览与筛选"
    __ui_icon__ = "rss"
    __ui_order__ = 2

    poll_interval_seconds: int = _ui_field(
        label="通知检查间隔（秒）",
        hint="检查提及、评论和楼中楼通知的间隔；最低 30 秒，常用值为 60。",
        order=0,
        default=60,
        step=30,
    )
    feed_interval_seconds: int = _ui_field(
        label="推荐流浏览间隔（秒）",
        hint="两次逛推荐流的平均间隔；每次实际间隔在它的 0.5–1.5 倍之间随机，不会整点触发。发布版推荐 600 秒（10 分钟），可按账号活跃度和模型额度自行调整。",
        order=1,
        default=600,
        step=300,
    )
    feed_active_start_time: str = _ui_field(
        label="AI 浏览开始时间",
        hint="只限制推荐流读取和主动评论的时间窗口，使用服务器本地时间 HH:MM；默认 08:00。与结束时间相同表示全天。",
        order=2,
        default="08:00",
        placeholder="例如 08:00",
    )
    feed_active_end_time: str = _ui_field(
        label="AI 浏览结束时间",
        hint="只限制推荐流读取和主动评论的时间窗口，使用服务器本地时间 HH:MM；默认 23:00。支持跨午夜窗口。",
        order=3,
        default="23:00",
        placeholder="例如 23:00",
    )
    game_topic_hints: list[str] = _ui_field(
        label="浏览内容关键词（可选）",
        hint="留空则不按关键词过滤，浏览和互动范围完全由下方「社区白名单」决定。填了就只处理命中这些关键词的帖子。",
        order=4,
        default_factory=list,
    )


class XiaoheiheAutomationSection(PluginConfigBase):
    __ui_label__ = "自动运营额度"
    __ui_icon__ = "send"
    __ui_order__ = 3

    posts_per_day: int = _ui_field(
        label="每日主动发帖数",
        hint="每天最多主动发布的帖子数量；发布版默认 1。设为 0 表示关闭自动发帖。",
        order=0,
        default=1,
        step=1,
    )
    proactive_comments_per_day: int = _ui_field(
        label="每日主动评论数",
        hint="每天最多对推荐流帖子发表评论的数量；设为 0 表示关闭主动评论。",
        order=1,
        default=1,
        step=1,
    )
    likes_per_day: int = _ui_field(
        label="每日点赞数",
        hint="逛推荐流时给命中浏览范围（社区白名单 + 可选关键词）的帖子点赞，每天最多这么多个，同一帖子只赞一次；设为 0 表示不点赞。",
        order=9,
        default=3,
        step=1,
    )
    notification_replies_per_day: int = _ui_field(
        label="每日通知回复数",
        hint="每天最多回复提及、评论和楼中楼通知的数量；设为 0 表示关闭自动回复。",
        order=2,
        default=1,
        step=1,
    )
    send_cooldown_seconds: int = _ui_field(
        label="发送冷却时间（秒）",
        hint="两次发送之间的最短间隔，用于降低重复发送和账号风控风险。",
        order=3,
        default=120,
        step=30,
    )
    post_active_start_time: str = _ui_field(
        label="自动发帖开始时间",
        hint="只限制主动发帖和失败重试的时间窗口，使用服务器本地时间 HH:MM；默认 10:00。与结束时间相同表示全天。",
        order=4,
        default="10:00",
        placeholder="例如 10:00",
    )
    post_active_end_time: str = _ui_field(
        label="自动发帖结束时间",
        hint="只限制主动发帖和失败重试的时间窗口，使用服务器本地时间 HH:MM；默认 22:00。支持跨午夜窗口。",
        order=5,
        default="22:00",
        placeholder="例如 22:00",
    )
    community_allowlist: list[str] = _ui_field(
        label="社区白名单",
        hint="允许浏览和主动互动的社区；默认包含 PC 游戏、盒友杂谈。最多选择 10 个，留空表示不主动互动。",
        order=6,
        default_factory=lambda: list(DEFAULT_COMMUNITY_ALLOWLIST),
        json_schema_extra={
            "items": {"type": "string", "enum": list(COMMUNITY_OPTIONS)},
            "max_items": MAX_ALLOWLIST_ITEMS,
        },
    )
    post_target_primary: CommunityName = _ui_field(
        label="发帖社区",
        hint="选择要发到哪个社区（版块）。留空表示不自动发帖。",
        order=7,
        default="",
    )
    post_target_secondary: HashtagName = _ui_field(
        label="发帖话题标签",
        hint="从常用 #话题标签 里选一个（可选）。想要别的标签，用下方「自定义话题标签」自由填写。留空则不挂话题。",
        order=8,
        default="",
    )
    custom_hashtags: str = _ui_field(
        label="自定义话题标签",
        hint="自由填写额外的话题标签，多个用逗号或空格分隔（如：数码评测, 装机）。会追加到上面选的话题标签之后。留空则不追加。",
        order=9,
        default="",
    )
    post_topic_ids: list[str] = _ui_field(
        label="旧版发帖目标 ID（兼容字段）",
        hint="旧版数字 ID 配置兼容字段，正常使用请通过上面的中文选项选择。",
        order=99,
        default_factory=list,
        json_schema_extra={"hidden": True},
    )
    summary_enabled: bool = _ui_field(
        label="浏览总结发帖",
        hint="开启后按「每 N 帖 / 每 N 小时」把浏览过的帖子写成一篇 200–400 字、2–4 段的总结，发到上面的「发帖社区」。开启时会暂停按天的自动发帖。",
        order=20,
        default=False,
    )
    summary_trigger: SummaryTrigger = _ui_field(
        label="总结触发方式",
        hint="count：攒够 N 帖就发；interval：距上一批满 N 小时就发；either：两者先到先发。",
        order=21,
        default="count",
    )
    summary_every_posts: int = _ui_field(
        label="每多少帖总结一次",
        hint="按帖数触发时使用，默认 10 帖；单次最多取 100 帖。",
        order=22,
        default=10,
        step=1,
    )
    summary_interval_hours: int = _ui_field(
        label="总结间隔（小时）",
        hint="按时间触发时使用，默认 24 小时；从本批第一条素材开始计时。",
        order=23,
        default=24,
        step=1,
    )


class XiaoheiheContentSection(PluginConfigBase):
    """内容生成与其它 MaiBot 插件的只读联动配置。

    当前联动插件市场中的「麦麦绘图」「生活频率」「自我身份档案」。
    API 名称仍按“插件 ID + @API(name=...)”组成，具体获取方法写在各字段提示中。
    """

    __ui_label__ = "内容与插件联动"
    __ui_icon__ = "puzzle"
    __ui_order__ = 4

    persona_override: str = _ui_field(
        label="小黑盒人设（可选）",
        hint="留空时使用 MaiBot 主配置里的昵称和人格设定，和 QQ 上是同一个人。只有想让小黑盒上的身份或口吻不一样时才填写，填写后只影响本插件生成的评论、回复和帖子。",
        order=0,
        default="",
        json_schema_extra={"x-widget": "textarea"},
    )
    generate_post_images: bool = _ui_field(
        label="自动生成帖子图片",
        hint="开启后生成帖子图片。直接用下面的「生图 API」生成（可用 MaiBot 已加载的画图模型，也可单独填中转站）；生图失败会自动降级为纯文字发帖。",
        order=1,
        default=False,
    )
    image_api_base_url: str = _ui_field(
        label="生图 API 地址",
        hint="OpenAI 兼容生图接口地址，如 https://ai.feiyuapi.top/v1。留空则尝试用 MaiBot 主配置里 api_provider 为「画图」的那个中转站。",
        order=2,
        default="",
        json_schema_extra={"x-widget": "textarea"},
    )
    image_api_key: str = _ui_field(
        label="生图 API Key",
        hint="生图中转站的密钥。留空则尝试用 MaiBot 主配置里「画图」provider 的 key。",
        order=3,
        default="",
        json_schema_extra={"x-widget": "textarea"},
    )
    image_model: str = _ui_field(
        label="生图模型名",
        hint="生图模型标识，如 gpt-image-2.5-flare-native。留空则尝试用 MaiBot 主配置里「画图」provider 对应的模型。",
        order=4,
        default="",
        placeholder="gpt-image-2.5-flare-native",
    )
    life_context_api: str = _ui_field(
        label="生活轨迹 API",
        hint="可选联动「生活频率」（ID：org.orge-8.life-frequency）。优先调用这里填写的只读 API；新版该插件没有 get_current_context 时会自动改读它的 life_state.json 状态文件，都读不到才退回当前时间，不影响纯文本发帖。",
        order=5,
        default="org.orge-8.life-frequency.get_current_context",
    )
    identity_reference_api: str = _ui_field(
        label="身份参考图 API",
        hint="可选联动「自我身份档案」（ID：org.mai-mai.self-identity）；当前完整 API 是 org.mai-mai.self-identity.get_default_reference_image，只读调用。只在生成图片时作为参考图使用，未安装时会自动跳过。",
        order=6,
        default="org.mai-mai.self-identity.get_default_reference_image",
    )
    post_summarize_feed: bool = _ui_field(
        label="发帖总结今天刷到的帖子",
        hint="旧版简版总结；开启「浏览总结发帖」后不再使用。",
        order=7,
        default=True,
    )
    use_host_replyer: bool = _ui_field(
        label="使用宿主 Replyer 生成",
        hint="开启后评论、私信、发帖和浏览总结都走 MaiBot 的 Replyer（人格、记忆、Hook）；宿主缺少 maisaka.reply.generate 能力时自动回退到插件自己的模型。",
        order=8,
        default=True,
    )
    host_replyer_stream_id: str = _ui_field(
        label="宿主 Replyer 聊天流 ID（可选）",
        hint="留空时插件会创建一个 internal 会话给 Replyer 用；如果宿主适配器策略拒绝，可在这里填一个已存在的聊天流 ID。",
        order=9,
        default="",
    )


class XiaoheiheDirectMessageSection(PluginConfigBase):
    __ui_label__ = "私信兼容设置"
    __ui_icon__ = "message-circle"
    __ui_order__ = 5

    dm_enabled: bool = _ui_field(
        label="启用私信功能",
        hint="开启后轮询小黑盒私信，并对「私信对象白名单」里的对话自动回复；关闭时完全不读取私信。首次接入建议先用下面的 dry_run 只生成草稿。",
        order=0,
        default=False,
    )
    dm_mode: RunMode = _ui_field(
        label="私信运行模式",
        hint="dry_run 只生成并记录草稿；auto 才真正发送。插件级「运行模式」是 dry_run 时也会一起闭锁。",
        order=1,
        default="dry_run",
        placeholder="请选择 dry_run 或 auto",
    )
    dm_poll_interval_seconds: int = _ui_field(
        label="私信检查间隔（秒）",
        hint="私信收件箱检查间隔；最低 60 秒，常用 180 秒。",
        order=2,
        default=180,
        step=30,
    )
    dm_daily_quota: int = _ui_field(
        label="每日私信回复数",
        hint="每天最多自动回复的私信条数；设为 0 表示只读取、不回复。",
        order=3,
        default=1,
        step=1,
    )
    dm_cooldown_seconds: int = _ui_field(
        label="私信回复冷却（秒）",
        hint="同一个人的两条自动回复之间的最短间隔，用于降低风控风险。",
        order=4,
        default=600,
        step=60,
    )
    dm_max_reply_chars: int = _ui_field(
        label="私信回复最大字数",
        hint="单条私信回复的最大字数，超出会截断。",
        order=5,
        default=180,
        step=20,
    )
    dm_reply_scope: DmReplyScope = _ui_field(
        label="私信回复范围",
        hint="仅白名单：只回复白名单里的 UID；除黑名单外全部：除黑名单以外的人都回复。黑名单始终优先。",
        order=6,
        default="allowlist",
    )
    dm_user_allowlist: list[str] = _ui_field(
        label="私信对象白名单",
        hint="「仅白名单」模式下只有这些 UID 会收到自动回复。点击添加项目逐条填写 UID；留空且范围是仅白名单时只读取、不回复。",
        order=7,
        default_factory=list,
    )
    dm_user_blocklist: list[str] = _ui_field(
        label="私信对象黑名单",
        hint="这些 UID 永远不会收到自动回复，优先级高于白名单。点击添加项目逐条填写 UID。",
        order=8,
        default_factory=list,
    )
    dm_generate_images: bool = _ui_field(
        label="私信回复配图",
        hint="开启后先按回复内容生成一张图并上传，再随私信一起发送；生图或上传失败会自动降级为纯文字，不影响回复本身。",
        order=9,
        default=False,
    )


class XiaoheiheConfig(PluginConfigBase):
    """插件根配置；分组只调整配置页布局，旧版扁平 [plugin] 会自动迁移。"""

    plugin: XiaoheihePluginSection = Field(default_factory=XiaoheihePluginSection)
    basic: XiaoheiheBasicSection = Field(default_factory=XiaoheiheBasicSection)
    browse: XiaoheiheBrowseSection = Field(default_factory=XiaoheiheBrowseSection)
    automation: XiaoheiheAutomationSection = Field(default_factory=XiaoheiheAutomationSection)
    content: XiaoheiheContentSection = Field(default_factory=XiaoheiheContentSection)
    dm: XiaoheiheDirectMessageSection = Field(default_factory=XiaoheiheDirectMessageSection)

    @property
    def enabled(self) -> bool:
        return self.basic.enabled

    @property
    def profile_id(self) -> str:
        return self.basic.profile_id

    @property
    def mode(self) -> str:
        return self.basic.mode

    @property
    def manual_cookie(self) -> str:
        return str(self.basic.manual_cookie or "")

    @property
    def poll_interval_seconds(self) -> int:
        return self.browse.poll_interval_seconds

    @property
    def feed_interval_seconds(self) -> int:
        return self.browse.feed_interval_seconds

    @property
    def feed_active_start_time(self) -> str:
        return self.browse.feed_active_start_time

    @property
    def feed_active_end_time(self) -> str:
        return self.browse.feed_active_end_time

    @property
    def posts_per_day(self) -> int:
        return self.automation.posts_per_day

    @property
    def proactive_comments_per_day(self) -> int:
        return self.automation.proactive_comments_per_day

    @property
    def likes_per_day(self) -> int:
        return self.automation.likes_per_day

    @property
    def notification_replies_per_day(self) -> int:
        return self.automation.notification_replies_per_day

    @property
    def send_cooldown_seconds(self) -> int:
        return self.automation.send_cooldown_seconds

    @property
    def post_active_start_time(self) -> str:
        return self.automation.post_active_start_time

    @property
    def post_active_end_time(self) -> str:
        return self.automation.post_active_end_time

    @property
    def dm_enabled(self) -> bool:
        return self.dm.dm_enabled

    @property
    def dm_mode(self) -> str:
        return self.dm.dm_mode

    @property
    def dm_poll_interval_seconds(self) -> int:
        return self.dm.dm_poll_interval_seconds

    @property
    def dm_daily_quota(self) -> int:
        return self.dm.dm_daily_quota

    @property
    def dm_cooldown_seconds(self) -> int:
        return self.dm.dm_cooldown_seconds

    @property
    def dm_max_reply_chars(self) -> int:
        return self.dm.dm_max_reply_chars

    @property
    def dm_reply_scope(self) -> str:
        return str(self.dm.dm_reply_scope)

    @property
    def dm_user_allowlist(self) -> list[str]:
        return self.dm.dm_user_allowlist

    @property
    def dm_user_blocklist(self) -> list[str]:
        return self.dm.dm_user_blocklist

    @property
    def dm_generate_images(self) -> bool:
        return self.dm.dm_generate_images

    @property
    def community_allowlist(self) -> list[str]:
        return self.automation.community_allowlist

    @property
    def persona_override(self) -> str:
        return self.content.persona_override

    @property
    def generate_post_images(self) -> bool:
        return self.content.generate_post_images

    @property
    def image_api_base_url(self) -> str:
        return self.content.image_api_base_url

    @property
    def image_api_key(self) -> str:
        return self.content.image_api_key

    @property
    def image_model(self) -> str:
        return self.content.image_model

    @property
    def life_context_api(self) -> str:
        return self.content.life_context_api

    @property
    def identity_reference_api(self) -> str:
        return self.content.identity_reference_api

    @property
    def post_summarize_feed(self) -> bool:
        return bool(self.content.post_summarize_feed)

    @property
    def use_host_replyer(self) -> bool:
        return bool(self.content.use_host_replyer)

    @property
    def host_replyer_stream_id(self) -> str:
        return str(self.content.host_replyer_stream_id or "").strip()

    @property
    def summary_enabled(self) -> bool:
        return bool(self.automation.summary_enabled)

    @property
    def summary_trigger(self) -> str:
        return str(self.automation.summary_trigger)

    @property
    def summary_every_posts(self) -> int:
        return max(1, min(int(self.automation.summary_every_posts), 100))

    @property
    def summary_interval_hours(self) -> int:
        return max(1, min(int(self.automation.summary_interval_hours), 168))

    @property
    def post_topic_ids(self) -> list[str]:
        # 发帖分区：只用「发帖社区」，不含话题标签（标签走 hashtags）
        primary = str(self.automation.post_target_primary or "").strip()
        if primary:
            return [primary]
        return list(self.automation.post_topic_ids)

    @property
    def post_hashtags(self) -> list[str]:
        # 发帖话题标签：下拉选的常用标签 + 自定义自由填写的标签
        tags: list[str] = []
        selected = str(self.automation.post_target_secondary or "").strip()
        if selected:
            tags.append(selected)
        custom = str(self.automation.custom_hashtags or "").strip()
        if custom:
            tags.extend(t.strip() for t in re.split(r"[,，\s]+", custom) if t.strip())
        return list(dict.fromkeys(tags))

    @property
    def game_topic_hints(self) -> list[str]:
        return self.browse.game_topic_hints


class XiaoheiheOperatorPlugin(MaiBotPlugin):
    config_model = XiaoheiheConfig
    config_reload_subscriptions: ClassVar[Iterable[str]] = ()
    _LEGACY_CONFIG_SECTIONS: ClassVar[dict[str, str]] = {
        "enabled": "basic",
        "profile_id": "basic",
        "mode": "basic",
        "poll_interval_seconds": "browse",
        "feed_interval_seconds": "browse",
        "feed_active_start_time": "browse",
        "feed_active_end_time": "browse",
        "game_topic_hints": "browse",
        "posts_per_day": "automation",
        "proactive_comments_per_day": "automation",
        "likes_per_day": "automation",
        "notification_replies_per_day": "automation",
        "send_cooldown_seconds": "automation",
        "post_active_start_time": "automation",
        "post_active_end_time": "automation",
        "community_allowlist": "automation",
        "post_target_primary": "automation",
        "post_target_secondary": "automation",
        "post_topic_ids": "automation",
        "persona_override": "content",
        "generate_post_images": "content",
        "life_context_api": "content",
        "identity_reference_api": "content",
        "dm_enabled": "dm",
        "dm_mode": "dm",
        "dm_poll_interval_seconds": "dm",
        "dm_daily_quota": "dm",
        "dm_cooldown_seconds": "dm",
        "dm_max_reply_chars": "dm",
        "dm_user_allowlist": "dm",
    }

    def __init__(self) -> None:
        super().__init__()
        self._store: OperatorStore | None = None
        self._dm_store: DirectMessageStore | None = None
        self._drawing: DrawingGateway | None = None
        self._image_gen: DirectImageGenerator | None = None
        self._runtime: XiaoheiheRuntime | None = None
        self._generator: ContentGenerator | None = None
        self._host_replyer: HostReplyer | None = None
        self._persona: PersonaContextProvider | None = None
        self._worker: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._next_feed_at = 0.0
        self._rng = random.Random()
        self._last_dm_poll_at = 0.0
        self._dm_blocked_until = 0.0
        self._manual_cookie_applied = ""
        self._topic_id_cache: dict[str, str] = {}

    @staticmethod
    def _sanitize_config(config: Any) -> Any:
        """在 SDK 版本检查前补齐 [plugin].config_version。"""

        if not isinstance(config, Mapping):
            return config
        data = dict(config)
        raw_plugin = data.get("plugin")
        plugin = dict(raw_plugin) if isinstance(raw_plugin, Mapping) else {}
        if not str(plugin.get("config_version") or "").strip():
            plugin["config_version"] = "0.1.0"
        data["plugin"] = plugin
        return data

    def normalize_plugin_config(self, config_data: Any) -> tuple[dict[str, Any], bool]:
        """把旧版扁平 [plugin] 业务字段迁移到新的配置分组。"""

        sanitized = self._sanitize_config(config_data)
        if not isinstance(sanitized, Mapping):
            return sanitized, False

        data = dict(sanitized)
        raw_plugin = data.get("plugin")
        plugin = dict(raw_plugin) if isinstance(raw_plugin, Mapping) else {}
        changed = False
        for field_name, section_name in self._LEGACY_CONFIG_SECTIONS.items():
            if field_name not in plugin:
                continue
            raw_section = data.get(section_name)
            section = dict(raw_section) if isinstance(raw_section, Mapping) else {}
            if field_name not in section:
                section[field_name] = plugin[field_name]
            data[section_name] = section
            plugin.pop(field_name, None)
            changed = True
        automation = data.get("automation")
        if isinstance(automation, Mapping):
            normalized_automation = dict(automation)
            raw_targets = normalized_automation.get("post_topic_ids")
            if isinstance(raw_targets, str):
                legacy_targets = [
                    item.strip()
                    for item in raw_targets.replace("，", ",").split(",")
                    if item.strip()
                ]
                normalized_automation["post_topic_ids"] = legacy_targets
                data["automation"] = normalized_automation
                changed = True
            else:
                legacy_targets = list(raw_targets) if isinstance(raw_targets, list) else []
            # 数字 ID 继续保存在兼容字段中；不要把它写进 Literal 下拉字段，
            # 否则旧配置会在 Pydantic 校验阶段被拒绝。新配置直接从中文选项选择。
            if legacy_targets and not str(normalized_automation.get("post_target_primary") or "").strip():
                named_targets = [item for item in legacy_targets if item in POST_TARGET_OPTIONS]
                if named_targets:
                    normalized_automation["post_target_primary"] = named_targets[0]
                    if len(named_targets) > 1 and not str(normalized_automation.get("post_target_secondary") or "").strip():
                        normalized_automation["post_target_secondary"] = named_targets[1]
                    data["automation"] = normalized_automation
                    changed = True
            # 二创把「发帖目标 2」改成「话题标签」，两个下拉框的取值也随之收窄。
            # 上游配置里的 Steam / PC 游戏 等值不在新枚举中，直接交给 Pydantic
            # 会被判定非法；这里降级到兼容字段，发送前仍会按名字解析成真实
            # topic_id / hashtag，行为与上游一致，不会因为升级而丢配置。
            allowed_primary = set(get_args(CommunityName))
            allowed_hashtags = set(get_args(HashtagName))
            primary = str(normalized_automation.get("post_target_primary") or "").strip()
            if primary and primary not in allowed_primary:
                compat_targets = [
                    str(item).strip()
                    for item in (normalized_automation.get("post_topic_ids") or [])
                    if str(item).strip()
                ]
                if primary not in compat_targets:
                    compat_targets.insert(0, primary)
                normalized_automation["post_topic_ids"] = compat_targets
                normalized_automation["post_target_primary"] = ""
                changed = True
            secondary = str(normalized_automation.get("post_target_secondary") or "").strip()
            if secondary and secondary not in allowed_hashtags:
                existing_tags = [
                    item.strip()
                    for item in re.split(r"[,，\s]+", str(normalized_automation.get("custom_hashtags") or ""))
                    if item.strip()
                ]
                merged_tags = list(dict.fromkeys([secondary, *existing_tags]))
                normalized_automation["custom_hashtags"] = ", ".join(merged_tags)
                normalized_automation["post_target_secondary"] = ""
                changed = True
            data["automation"] = normalized_automation
        data["plugin"] = plugin

        try:
            return super().normalize_plugin_config(data)
        except AttributeError:
            return data, changed

    def get_webui_config_schema(self, **kwargs: Any) -> dict[str, Any]:
        """补齐配置页提示，并把暂不使用的私信分组默认收起。"""

        try:
            schema = super().get_webui_config_schema(**kwargs)
        except AttributeError:
            return {"sections": {}}
        try:
            sections = schema.get("sections") or {}
            for name, section in sections.items():
                if not isinstance(section, dict):
                    continue
                if name == "dm":
                    section["collapsed"] = True
                    section["order"] = 100 + float(section.get("order") or 0)
                    section["description"] = (
                        "当前发布版不提供私信自动发送；只有未来接入稳定发送能力后才需要调整。"
                    )
                else:
                    section["collapsed"] = False
                for field_name, field in (section.get("fields") or {}).items():
                    if isinstance(field, dict) and field.get("description") and not field.get("hint"):
                        field["hint"] = field["description"]
                    # Radix Select 禁止 Select.Item 使用空字符串，但空值本身仍然
                    # 可以作为 value 显示 placeholder。因此保留字段默认空值，
                    # 只从选项列表中移除空项，用户仍可通过清空输入保持不发帖。
                    if (
                        name == "automation"
                        and field_name in {"post_target_primary", "post_target_secondary"}
                        and isinstance(field, dict)
                        and isinstance(field.get("choices"), list)
                    ):
                        field["choices"] = [choice for choice in field["choices"] if str(choice) != ""]
            return schema
        except Exception:
            self.ctx.logger.exception("小黑盒配置页显示元数据修正失败，回退原始 Schema")
            return schema

    async def on_load(self) -> None:
        data_dir = Path(self.ctx.paths.data_dir)
        self._store = OperatorStore(data_dir / "state.db")
        self._dm_store = DirectMessageStore(data_dir / "state.db")
        self._drawing = DrawingGateway(self.ctx.api)
        self._image_gen = self._resolve_image_generator()
        self._runtime = XiaoheiheRuntime(data_dir, self.config.profile_id)
        manual_cookie = str(self.config.manual_cookie or "").strip()
        if manual_cookie:
            await self._apply_manual_cookie(manual_cookie)
        self._persona = PersonaContextProvider(
            self.ctx,
            life_api_name=str(self.config.life_context_api),
            identity_api_name=str(self.config.identity_reference_api),
        )
        self._host_replyer = HostReplyer(
            self.ctx,
            profile_id=str(self.config.profile_id),
            account_id=str(self._runtime.credentials.uid)
            if self._runtime.credentials
            else "",
            stream_id=str(self.config.host_replyer_stream_id or ""),
        )
        self._generator = ContentGenerator(
            self.ctx.llm,
            persona=self._persona_identity,
            writer=self._host_writer if self.config.use_host_replyer else None,
        )
        self.ctx.logger.info(
            "小黑盒生成链路：%s",
            "宿主 Replyer（人格/记忆/Hook）" if self.config.use_host_replyer else "插件模型",
        )
        # 重启后不立刻去逛，过一会儿再打开，避免每次重启都在同一时刻出现一轮动作。
        self._next_feed_at = time.time() + self._rng.uniform(120, 900)
        if self._image_gen is not None:
            self.ctx.logger.info("小黑盒生图：使用直连生图 API（model=%s）", self._image_gen.model)
        else:
            self.ctx.logger.info("小黑盒生图：未配置直连生图 API，配图将降级为纯文字或走麦麦绘图")
        self.ctx.logger.info("小黑盒运营插件已加载：enabled=%s mode=%s", self.config.enabled, self.config.mode)
        await self._log_persona_source()
        if self.config.enabled:
            self._start_worker()

    async def _apply_manual_cookie(self, cookie_text: str) -> bool:
        """优先使用配置里的手动 Cookie；失败时保留扫码登录的凭据。"""

        if self._runtime is None:
            return False
        credentials, message = await self._runtime.apply_manual_cookie(cookie_text)
        self._manual_cookie_applied = cookie_text
        if credentials is None:
            self.ctx.logger.warning("小黑盒手动 Cookie 未生效：%s", message)
            return False
        self.ctx.logger.info(
            "小黑盒已使用手动 Cookie 登录：uid=%s nickname=%s",
            (credentials.uid or "?")[:24],
            (credentials.nickname or "-")[:24],
        )
        return True

    def _resolve_image_generator(self) -> DirectImageGenerator | None:
        """解析生图参数：优先用插件配置里填的，其次从 MaiBot 主配置里找「画图」provider。"""

        base = str(self.config.image_api_base_url or "").strip()
        key = str(self.config.image_api_key or "").strip()
        model = str(self.config.image_model or "").strip()
        gen = build_generator(base, key, model)
        if gen is not None:
            return gen

        # 回退：读 MaiBot 主配置 model_config.toml 里 api_provider 名为「画图」的中转站。
        # 路径只由 ctx.paths.data_dir 推导，不写死开发机目录；找不到时安静放弃，
        # 配图会自动降级为纯文字，不影响发帖。
        try:
            import tomllib
            from pathlib import Path as _Path

            cands: list[_Path] = []
            data_dir = getattr(getattr(self.ctx, "paths", None), "data_dir", None)
            if data_dir:
                root = _Path(data_dir)
                for candidate in (
                    root.parent / "config" / "model_config.toml",
                    root.parent.parent / "config" / "model_config.toml",
                    root / "config" / "model_config.toml",
                ):
                    if candidate not in cands:
                        cands.append(candidate)
            for config_path in cands:
                if not config_path.exists():
                    continue
                cfg = tomllib.loads(config_path.read_text(encoding="utf-8"))
                provs = {str(x.get("name")): x for x in (cfg.get("api_providers") or [])}
                for m in (cfg.get("models") or []):
                    if str(m.get("api_provider")) == "画图":
                        prov = provs.get("画图") or {}
                        base = str(prov.get("base_url") or "").rstrip("/")
                        key = str(prov.get("api_key") or "")
                        model = str(m.get("model_identifier") or m.get("name") or "")
                        gen = build_generator(base, key, model)
                        if gen is not None:
                            return gen
                        break
                break
        except Exception:
            pass
        return None

    def _log_debug(self, message: str, *args: object) -> None:
        """Some hosts/tests provide a logger without debug(); degrade quietly."""

        logger = getattr(self.ctx, "logger", None)
        log = getattr(logger, "debug", None)
        if callable(log):
            log(message, *args)

    async def _host_writer(self, reference: str, format_instruction: str) -> str:
        """ContentGenerator 的宿主 Replyer 出口；失败时抛出，由它回退到本地模型。"""

        if self._host_replyer is None:
            raise RuntimeError("宿主 Replyer 未初始化")
        try:
            return await self._host_replyer.generate(
                reference=reference,
                format_instruction=format_instruction,
            )
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(
                "宿主 Replyer 不可用，本次回退到插件模型：%s", type(exc).__name__
            )
            raise

    async def _persona_identity(self) -> str:
        """生成内容用的人设：插件里单独填写的优先，否则跟随宿主 MaiBot 的昵称和人格。"""

        override = str(self.config.persona_override or "").strip()
        if override:
            return override
        if self._persona is None:
            return ""
        return await self._persona.host_persona()

    async def _log_persona_source(self) -> None:
        try:
            override = str(self.config.persona_override or "").strip()
            text = override or (await self._persona.host_persona() if self._persona is not None else "")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒人设读取失败，先用默认人设：%s", type(exc).__name__)
            return
        if not text:
            self.ctx.logger.warning("小黑盒没读到宿主人设，先用默认人设；可检查 bot.nickname 和 personality 配置")
            return
        source = "插件配置" if override else "宿主 MaiBot"
        first_line = text.splitlines()[0][:24]
        self.ctx.logger.info("小黑盒人设来源：%s（%d 字，%s…）", source, len(text), first_line)

    async def on_unload(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        if self._store is not None:
            self._store.close()
        if self._dm_store is not None:
            self._dm_store.close()
        if self._runtime is not None:
            await self._runtime.close()

    async def on_config_update(self, scope: str, config_data: dict, version: str) -> None:
        if scope != "self":
            return
        self.ctx.logger.info("小黑盒运营插件配置已更新：version=%s", version)
        if self._persona is not None:
            self._generator = ContentGenerator(
                self.ctx.llm,
                persona=self._persona_identity,
                writer=self._host_writer if self.config.use_host_replyer else None,
            )
        manual_cookie = str(self.config.manual_cookie or "").strip()
        if manual_cookie and manual_cookie != self._manual_cookie_applied:
            await self._apply_manual_cookie(manual_cookie)
        if self.config.enabled:
            self._start_worker()
        elif self._worker is not None:
            self._worker.cancel()
            self._worker = None

    def _start_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._stop.clear()
            self._worker = asyncio.create_task(self._run_worker(), name="xiaoheihe-operator")

    async def _run_worker(self) -> None:
        while not self._stop.is_set():
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.error("小黑盒运营轮询失败：%s", exc)
            await asyncio.sleep(max(30, int(self.config.poll_interval_seconds)))

    async def _poll_once(self) -> None:
        if self._store is None or self._runtime is None or self._runtime.credentials is None:
            return
        client = await self._runtime.client()
        post_window_active = _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        )
        browse_window_active = _time_window_active(
            self.config.feed_active_start_time,
            self.config.feed_active_end_time,
        )
        if post_window_active:
            await self._retry_pre_send_proactive_posts(client)
        await self._reconcile_unknowns(client)
        await self._retry_suppressed_comments(client)
        for event_type in (NotificationType.MENTION, NotificationType.REPLY):
            await self._poll_notifications(client, event_type)
        now = time.time()
        if self.config.dm_enabled and now - self._last_dm_poll_at >= max(60, int(self.config.dm_poll_interval_seconds)):
            self._last_dm_poll_at = now
            await self._poll_direct_messages(client)
        if self.config.summary_enabled:
            await self._maybe_publish_digest(client)
        if now >= self._next_feed_at:
            self._next_feed_at = now + self._next_feed_gap()
            if post_window_active or browse_window_active:
                await self._proactive_once(
                    client,
                    post_window_active=post_window_active,
                    browse_window_active=browse_window_active,
                )

    async def _retry_suppressed_comments(self, client) -> None:
        """Retry comments suppressed by cooldown or by an operator request.

        Suppression is transient. The action stays in the audit log while this
        pass retries it after the cooldown expires (or after /xhh_retry). Only
        one action is attempted per poll so a backlog cannot burst through the
        account rate limit.
        """
        if self._store is None or str(self.config.mode).lower() != "auto":
            return
        for row in self._store.list_outgoing(status=OutgoingStatus.SUPPRESSED, limit=50):
            if str(row.get("error") or "") not in {"cooldown", "manual_retry"}:
                continue
            kind = str(row.get("kind") or "")
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if kind == "proactive_comment" and not _time_window_active(
                self.config.feed_active_start_time,
                self.config.feed_active_end_time,
            ):
                continue
            if kind == "notification_reply":
                if not self._within_quota(
                    "notification_reply", int(self.config.notification_replies_per_day)
                ):
                    continue
                route = RoutingTarget(
                    profile_id=self.config.profile_id,
                    post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                    root_comment_id=str(metadata.get("root_comment_id") or ""),
                    parent_comment_id=str(metadata.get("parent_comment_id") or ""),
                    notification_id=str(metadata.get("event_key") or ""),
                )
            elif kind == "proactive_comment":
                if not self._within_quota(
                    "proactive_comment", int(self.config.proactive_comments_per_day)
                ):
                    continue
                route = RoutingTarget(
                    profile_id=self.config.profile_id,
                    post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                )
            else:
                continue
            if not route.post_id or not str(row.get("content") or "").strip():
                continue
            await self._dispatch_comment(
                client,
                action_key=str(row.get("action_key") or ""),
                route=route,
                content=str(row.get("content") or ""),
            )
            if kind == "notification_reply":
                self._finish_incoming_after_dispatch(
                    str(metadata.get("event_key") or ""),
                    str(row.get("action_key") or ""),
                )
            return

    async def _poll_direct_messages(self, client) -> None:
        """Read the DM inbox, then answer allowlisted unanswered messages.

        Two phases: refresh conversations (one cheap list call, skipped when the
        conversation marker has not changed), then answer every unanswered
        incoming message. Nothing is dispatched twice because each reply owns
        the durable action key dm_reply:<message_id>.
        """

        if self._dm_store is None or self._store is None or self._runtime is None:
            return
        profile_id = self.config.profile_id
        try:
            conversations = await client.fetch_direct_conversations(limit=20)
        except Exception as exc:  # noqa: BLE001
            probe = getattr(client, "last_direct_message_probe", {})
            self._dm_store.set_probe(
                profile_id,
                {"status": "error", "probe": _safe_status_data(probe)},
            )
            self.ctx.logger.warning(
                "小黑盒私信收件箱读取失败：type=%s probe=%s",
                type(exc).__name__,
                _safe_status_data(probe),
            )
            return
        probe = getattr(client, "last_direct_message_probe", {})
        self._dm_store.set_probe(
            profile_id, {"status": "ok", "probe": _safe_status_data(probe)}
        )

        _, baseline_ready = self._dm_store.get_cursor(profile_id)
        refreshed = 0
        fresh_messages = 0
        for conversation in conversations.items:
            peer_uid = str(conversation.peer_uid or "").strip()
            if not peer_uid:
                continue
            if not self._dm_recipient_allowed(peer_uid):
                continue
            marker = self._dm_store.get_thread_marker(profile_id, peer_uid)
            if conversation.marker and marker == conversation.marker:
                continue
            try:
                page = await client.fetch_direct_messages(peer_uid=peer_uid, limit=30)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug(
                    "小黑盒私信会话读取失败：peer=%s type=%s",
                    peer_uid[:40],
                    type(exc).__name__,
                )
                continue
            refreshed += 1
            for message in page.items:
                if not getattr(message, "incoming", False):
                    continue
                inserted = self._dm_store.insert_message(
                    message, baseline=not baseline_ready
                )
                if baseline_ready and inserted:
                    fresh_messages += 1
            self._dm_store.set_thread_marker(profile_id, peer_uid, conversation.marker)
        if not baseline_ready:
            self._dm_store.set_cursor(profile_id, "", baseline_ready=True)
            self.ctx.logger.info(
                "小黑盒私信历史基线已建立：conversations=%s", len(conversations.items)
            )
            return
        replied = 0
        for message in self._pending_direct_messages():
            if await self._process_direct_message(client, message):
                replied += 1
        self.ctx.logger.info(
            "小黑盒私信轮询完成：conversations=%s refreshed=%s new=%s replied=%s",
            len(conversations.items),
            refreshed,
            fresh_messages,
            replied,
        )

    def _dm_recipient_allowed(self, sender_uid: str) -> bool:
        """Blacklist always wins; otherwise the configured scope decides."""

        uid = str(sender_uid or "").strip()
        if not uid:
            return False
        blocklist = {
            str(value).strip()
            for value in self.config.dm_user_blocklist
            if str(value).strip()
        }
        if uid in blocklist:
            return False
        if str(self.config.dm_reply_scope).lower() == "blocklist":
            return True
        allowlist = {
            str(value).strip()
            for value in self.config.dm_user_allowlist
            if str(value).strip()
        }
        return uid in allowlist

    def _pending_direct_messages(self, *, limit: int = 5) -> list["_StoredDirectMessage"]:
        """Oldest incoming messages that still have no durable reply action."""

        if self._dm_store is None or self._store is None:
            return []
        cutoff = time.time() - 72 * 60 * 60
        pending: list[_StoredDirectMessage] = []
        rows = self._dm_store.list_recent(self.config.profile_id, limit=50)
        for row in reversed(rows):
            if not int(row.get("incoming") or 0):
                continue
            # 首次轮询建立的历史基线只做记录，永远不回复。
            if int(row.get("baseline") or 0):
                continue
            peer_uid = str(row.get("peer_uid") or "").strip()
            if not peer_uid or not self._dm_recipient_allowed(peer_uid):
                continue
            created_at = float(row.get("created_at") or row.get("observed_at") or 0)
            if created_at and created_at < cutoff:
                continue
            message_id = str(row.get("message_id") or "")
            if not message_id:
                continue
            if self._store.has_outgoing_key(f"dm_reply:{message_id}"):
                continue
            pending.append(_StoredDirectMessage(row))
            if len(pending) >= limit:
                break
        return pending

    async def _process_direct_message(self, client, message) -> bool:
        """Generate and send one reply. Returns True only when verified sent."""

        if self._store is None or self._dm_store is None or self._generator is None:
            return False
        sender_uid = str(getattr(message, "sender_uid", "") or "").strip()
        peer_uid = str(getattr(message, "peer_uid", "") or "").strip() or sender_uid
        if not self._dm_recipient_allowed(sender_uid):
            self.ctx.logger.info(
                "私信只读记录，未生成自动回复：sender_uid=%s scope=%s",
                sender_uid[:80],
                str(self.config.dm_reply_scope),
            )
            return False
        if not peer_uid:
            return False
        now = time.time()
        if now < self._dm_blocked_until:
            self.ctx.logger.info(
                "小黑盒私信处于暂停期，暂不回复：remaining_seconds=%s",
                int(self._dm_blocked_until - now),
            )
            return False
        if not self._within_quota("dm_reply", int(self.config.dm_daily_quota)):
            return False
        message_id = str(getattr(message, "message_id", "") or "")
        if not message_id:
            return False
        action_key = f"dm_reply:{message_id}"
        if self._store.get_outgoing(action_key):
            return False
        previous = self._store.last_outgoing_at(
            kind="dm_reply",
            target_id=sender_uid,
            exclude_action_key=action_key,
        )
        if previous is not None and now - previous < max(
            0, int(self.config.dm_cooldown_seconds)
        ):
            return False
        recent = self._dm_store.list_recent(self.config.profile_id, limit=12)
        recent_lines = [
            f"{item.get('sender_name') or item.get('sender_uid') or '对方'}：{normalize_text(str(item.get('text') or ''), 240)}"
            for item in reversed(recent)
            if str(item.get("peer_uid") or "") == peer_uid
            and str(item.get("text") or "").strip()
        ]
        reply = await self._generator.direct_message_reply(
            sender_name=str(getattr(message, "sender_name", "") or ""),
            message=str(getattr(message, "text", "") or ""),
            recent_context="\n".join(recent_lines[-8:]),
            max_chars=int(self.config.dm_max_reply_chars),
        )
        reply = normalize_outgoing_text(reply) if reply else reply
        if not reply:
            self._record_dm_skipped(action_key, "empty_generated_reply", message)
            return False
        allowed, reason = is_publishable(reply)
        if not allowed:
            self._record_dm_skipped(action_key, f"content_boundary:{reason}", message)
            return False
        action = OutgoingAction(
            action_key,
            "dm_reply",
            sender_uid,
            reply,
            metadata={
                "message_id": message_id,
                "conversation_id": str(getattr(message, "conversation_id", "") or ""),
                "peer_uid": peer_uid,
                "sender_name": str(getattr(message, "sender_name", "") or ""),
                "reply_mode": str(self.config.dm_mode),
                "image_requested": bool(self.config.dm_generate_images),
            },
        )
        if not self._store.begin_outgoing(action):
            return False
        if str(self.config.dm_mode).lower() == "dry_run" or str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            self.ctx.logger.info(
                "小黑盒私信回复处于 dry_run，已记录草稿未发送：message_id=%s",
                message_id[:40],
            )
            return True
        image_url = ""
        if self.config.dm_generate_images:
            image_url = await self._build_dm_image(client, reply, message_id)
            self._store.merge_outgoing_metadata(
                action_key, {"image_attached": bool(image_url)}
            )
        # Persist the send boundary before the write; a crash after this point
        # leaves a send_unknown action that is never re-sent automatically.
        self._store.update_outgoing(action_key, OutgoingStatus.SENDING, error="send_started")
        try:
            result = await client.send_direct_message(
                peer_uid=peer_uid, text=reply, image_url=image_url
            )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", "") or "")
            if category == "dm_restricted":
                self._dm_blocked_until = now + 30 * 60
                self._store.update_outgoing(
                    action_key, OutgoingStatus.FAILED, error="dm_restricted"
                )
                self.ctx.logger.warning(
                    "小黑盒禁止当前账号发送私信，已暂停私信回复 30 分钟"
                )
                return False
            if category == "dm_verification_required":
                self._dm_blocked_until = now + 6 * 60 * 60
                self._store.update_outgoing(
                    action_key,
                    OutgoingStatus.FAILED,
                    error="dm_verification_required",
                )
                self.ctx.logger.warning(
                    "小黑盒要求在 App 内完成安全或实名认证，私信回复暂停 6 小时"
                )
                return False
            status = OutgoingStatus.FAILED if category in {
                "credential_invalid",
                "forbidden",
                "rate_limited",
                "upstream_rejected",
                "response_shape",
                "http",
            } else OutgoingStatus.SEND_UNKNOWN
            self._store.update_outgoing(action_key, status, error=_error_label(exc))
            self.ctx.logger.error(
                "小黑盒私信发送失败或结果未知：type=%s category=%s",
                type(exc).__name__,
                category,
            )
            return False
        acknowledged = str(
            getattr(result, "message_id", "")
            or getattr(result, "heychat_ack_id", "")
            or "",
        )
        self._store.update_outgoing(
            action_key,
            OutgoingStatus.VERIFIED,
            external_id=acknowledged,
            request_id=str(getattr(result, "heychat_ack_id", "") or ""),
        )
        self.ctx.logger.info(
            "小黑盒私信已发送并经服务端确认：message_id=%s ack=%s",
            message_id[:40],
            str(getattr(result, "heychat_ack_id", ""))[:40],
        )
        return True

    async def _build_dm_image(self, client, reply: str, message_id: str) -> str:
        """Generate and upload one picture for a DM reply; "" means text only.

        Image generation is optional: every failure path falls back to the
        text-only reply instead of blocking it.
        """

        request_id = f"dm-{message_id}"
        prompt = f"maibot 的日常随手拍配图：{normalize_text(reply, 120)}"
        image_bytes: bytes | None = None
        stage = "direct_image"
        try:
            if self._image_gen is not None:
                image_bytes = await self._image_gen.generate(prompt)
            if image_bytes is None and self._drawing is not None:
                stage = "draw_submit"
                reference = (
                    await self._persona.identity_reference()
                    if self._persona is not None
                    else None
                )
                task = await self._drawing.submit(
                    f"{prompt}。保持身份参考图中的人物形象一致。",
                    request_id=request_id,
                    source_images=[reference] if reference else None,
                )
                stage = "draw_task_id"
                task_id = _task_id(task)
                if not task_id:
                    raise RuntimeError("绘图任务未返回 task_id")
                stage = "draw_wait"
                finished = await self._drawing.wait(task_id, request_id=request_id)
                if str(finished.get("status", "")).casefold() != "completed":
                    raise RuntimeError("绘图任务未完成")
                stage = "draw_read_image"
                image_bytes = await self._drawing.read_image(task_id)
            if not image_bytes:
                raise RuntimeError("没有可用的生图方式")
            stage = "upload_image"
            uploaded = await client.upload_image_bytes(
                image_bytes, filename=f"dm-{message_id}.png"
            )
            url = str(uploaded.get("url") or "").strip()
            if not url.startswith("https://"):
                raise RuntimeError("上传结果不是 https 地址")
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning(
                "小黑盒私信配图失败，已降级为纯文字：stage=%s error=%s",
                stage,
                type(exc).__name__,
            )
            return ""
        self.ctx.logger.info(
            "小黑盒私信配图已上传：message_id=%s bytes=%s",
            message_id[:40],
            len(image_bytes),
        )
        return url

    def _record_dm_skipped(self, action_key: str, reason: str, message) -> None:
        """Terminal audit row for messages we deliberately never sent."""

        if self._store is None:
            return
        action = OutgoingAction(
            action_key,
            "dm_reply",
            str(getattr(message, "sender_uid", "") or ""),
            "",
            metadata={
                "message_id": str(getattr(message, "message_id", "") or ""),
                "skipped": True,
            },
        )
        if self._store.begin_outgoing(action):
            self._store.update_outgoing(action_key, OutgoingStatus.FAILED, error=reason)

    async def _poll_notifications(self, client, event_type: NotificationType) -> None:
        assert self._store is not None
        # The API client treats cursor as an offset. Reusing an advancing
        # offset can skip fresh notifications when new items shift the list.
        _, baseline_ready = self._store.get_cursor(self.config.profile_id, event_type.value)
        page = await client.fetch_notifications(event_type, cursor="", page=1, page_size=20)
        items = list(getattr(page, "items", []) or [])
        if not baseline_ready:
            for item in items:
                event = self._normalize_event(item, event_type)
                if event is None:
                    continue
                self._store.remember_incoming_baseline(
                    event.event_key,
                    event_type.value,
                    {
                        "event_key": event.event_key,
                        "kind": event.kind,
                        "post_id": event.post_id,
                        "comment_id": event.comment_id,
                        "root_comment_id": event.root_comment_id,
                        "sender_id": event.sender_id,
                        "sender_name": event.sender_name,
                        "text": event.text,
                    },
                )
            self._store.set_cursor(self.config.profile_id, event_type.value, "", baseline_ready=True)
            self.ctx.logger.info("小黑盒 %s 通知历史基线已建立", event_type.value)
            return
        # Newest-first scans stop at the first known event. If an entire page is
        # new, continue to older pages (bounded to 200 notifications per type).
        for page_number in range(1, 11):
            if page_number > 1:
                if not bool(getattr(page, "has_more", False)):
                    break
                page = await client.fetch_notifications(
                    event_type, cursor="", page=page_number, page_size=20
                )
                items = list(getattr(page, "items", []) or [])
            found_known_event = False
            for item in items:
                event = self._normalize_event(item, event_type)
                if event is None:
                    continue
                if self._store.has_incoming(event.event_key):
                    found_known_event = True
                    continue
                self._store.claim_incoming(
                    event.event_key,
                    event_type.value,
                    {
                        "event_key": event.event_key,
                        "kind": event.kind,
                        "post_id": event.post_id,
                        "comment_id": event.comment_id,
                        "root_comment_id": event.root_comment_id,
                        "sender_id": event.sender_id,
                        "sender_name": event.sender_name,
                        "text": event.text,
                    },
                )
            if found_known_event or not bool(getattr(page, "has_more", False)):
                break
        self._store.set_cursor(self.config.profile_id, event_type.value, "", baseline_ready=True)
        # Claims are durable before work starts. Revisit pending events even if
        # their notification is no longer present in the current API page.
        for row in self._store.list_pending_incoming(kind=event_type.value, limit=50):
            payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
            event = IncomingEvent(
                event_key=str(payload.get("event_key") or row.get("event_key") or ""),
                kind=str(payload.get("kind") or event_type.value),
                post_id=str(payload.get("post_id") or ""),
                comment_id=str(payload.get("comment_id") or ""),
                root_comment_id=str(payload.get("root_comment_id") or ""),
                sender_id=str(payload.get("sender_id") or ""),
                sender_name=str(payload.get("sender_name") or ""),
                text=str(payload.get("text") or ""),
            )
            if not event.event_key or not event.post_id or not event.text.strip():
                self._store.mark_incoming_done(event.event_key, state="ignored")
                continue
            try:
                await self._process_event(client, event)
            except Exception as exc:  # noqa: BLE001
                attempts = int(row.get("attempts") or 0)
                retry_delay = min(3600, 60 * (2 ** min(attempts, 6)))
                self._store.defer_incoming(
                    event.event_key,
                    f"processing_error:{type(exc).__name__}",
                    delay_seconds=retry_delay,
                )
                self.ctx.logger.warning(
                    "小黑盒通知回复暂缓重试：event=%s error_type=%s",
                    event.event_key,
                    type(exc).__name__,
                )

    @staticmethod
    def _normalize_event(item: object, event_type: NotificationType) -> IncomingEvent | None:
        if isinstance(item, dict) and isinstance(item.get("notification"), Notification):
            notification = item["notification"]
            if not notification.post_id or not notification.message_id:
                return None
            return IncomingEvent(
                event_key=notification.message_id,
                kind=notification.event_type.value,
                post_id=str(notification.post_id),
                comment_id=str(notification.parent_comment_id or notification.external_comment_id),
                root_comment_id=str(notification.root_comment_id or ""),
                sender_id=str(notification.sender_uid or ""),
                sender_name=str(notification.sender_nickname or ""),
                text=str(notification.content or ""),
                raw=notification.raw or item,
            )
        if not isinstance(item, dict):
            return None
        post_id = str(item.get("post_id") or item.get("linkid") or item.get("link_id") or "")
        notification_id = str(item.get("notification_id") or item.get("message_id") or "")
        comment_id = str(item.get("comment_id") or item.get("comment_a_id") or item.get("commentid") or "")
        if not post_id or not (notification_id or comment_id):
            return None
        return IncomingEvent(
            event_key=f"{event_type.value}:{notification_id or comment_id}",
            kind=event_type.value,
            post_id=post_id,
            comment_id=comment_id,
            root_comment_id=str(item.get("root_comment_id") or item.get("comment_a_id") or ""),
            sender_id=str(item.get("sender_uid") or item.get("userid_a") or ""),
            sender_name=str(item.get("sender_nickname") or item.get("user_a") or ""),
            text=str(item.get("content") or item.get("comment_a_text") or item.get("text") or ""),
            raw=item,
        )

    async def _process_event(self, client, event: IncomingEvent) -> None:
        if self._store is None:
            return
        action_key = f"reply:{event.event_key}"
        existing = self._store.get_outgoing(action_key)
        if existing:
            if existing.get("status") == OutgoingStatus.SUPPRESSED.value and str(existing.get("error") or "") == "cooldown":
                self._store.defer_incoming(
                    event.event_key,
                    "send_cooldown",
                    delay_seconds=max(30, int(self.config.poll_interval_seconds)),
                    increment_attempt=False,
                )
                return
            # Any attempted notification reply is terminal. Never repeat a
            # rejected write or an uncertain send for the same event.
            self._store.mark_incoming_done(event.event_key)
            return
        if self._generator is None:
            self._defer_notification(event.event_key, "reply_generator_unavailable", delay_seconds=300)
            return
        if not self._within_quota("notification_reply", int(self.config.notification_replies_per_day)):
            now = time.time()
            next_day = self._day_start() + 24 * 60 * 60
            self._store.defer_incoming(
                event.event_key,
                "daily_quota",
                delay_seconds=max(60, next_day - now),
                increment_attempt=False,
            )
            self.ctx.logger.info("小黑盒通知回复达到每日配额，已保留待处理：%s", event.event_key)
            return

        context = await client.fetch_thread_context(event.post_id, root_comment_id=event.root_comment_id)
        if context is None:
            self._defer_notification(event.event_key, "thread_context_missing", delay_seconds=300)
            return
        reply = await self._generator.reply(
            post_title=str(getattr(context, "title", "")),
            post_body=str(getattr(context, "body", "")),
            comment=event.text,
        )
        reply = normalize_outgoing_text(reply) if reply else reply
        if not reply:
            self._defer_notification(event.event_key, "empty_generated_reply", delay_seconds=300)
            return
        allowed, reason = is_publishable(reply)
        if not allowed:
            self.ctx.logger.info("小黑盒回复被内容边界拦截：%s", reason)
            self._store.mark_incoming_done(event.event_key, state="ignored")
            return
        action = OutgoingAction(
            action_key,
            "notification_reply",
            event.post_id,
            reply,
            metadata={
                "post_id": event.post_id,
                "root_comment_id": event.root_comment_id,
                "parent_comment_id": event.comment_id,
                "event_key": event.event_key,
                "reply_strategy": "post_context_and_comment",
                "post_title": normalize_text(str(getattr(context, "title", "")), 120),
                "post_body_fingerprint": content_fingerprint(
                    normalize_text(str(getattr(context, "body", "")), 900)
                ),
                "comment_fingerprint": content_fingerprint(normalize_text(event.text, 500)),
            },
        )
        if not self._store.begin_outgoing(action):
            # The same event already has an audited action. Its state is either
            # handled by a dedicated retry path or intentionally terminal.
            self._store.mark_incoming_done(event.event_key)
            return
        self._store.mark_incoming_done(event.event_key, state="dispatched")
        route = RoutingTarget(
            profile_id=self.config.profile_id,
            post_id=event.post_id,
            root_comment_id=event.root_comment_id,
            parent_comment_id=event.comment_id,
            notification_id=event.event_key,
        )
        await self._dispatch_comment(
            client,
            action_key=action_key,
            route=route,
            content=reply,
        )
        self._finish_incoming_after_dispatch(event.event_key, action_key)

    def _defer_notification(self, event_key: str, reason: str, *, delay_seconds: float) -> None:
        if self._store is None:
            return
        row = self._store.get_incoming(event_key)
        attempts = int(row.get("attempts") or 0) if row else 0
        delay = min(3600, max(delay_seconds, 60 * (2 ** min(attempts, 6))))
        self._store.defer_incoming(
            event_key,
            reason,
            delay_seconds=delay,
        )
        self.ctx.logger.info(
            "小黑盒通知已延后重试：event=%s reason=%s delay_seconds=%s attempt=%s",
            event_key,
            reason,
            int(delay),
            attempts + 1,
        )

    def _finish_incoming_after_dispatch(self, event_key: str, action_key: str) -> None:
        if self._store is None:
            return
        outgoing = self._store.get_outgoing(action_key) or {}
        status = str(outgoing.get("status") or "")
        if status == OutgoingStatus.SUPPRESSED.value and str(outgoing.get("error") or "") == "cooldown":
            return
        self._store.mark_incoming_done(event_key)

    def _day_start(self) -> float:
        local = time.localtime()
        return time.mktime((local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, 0, 0, -1))

    def _within_quota(self, kind: str, limit: int) -> bool:
        if self._store is None:
            return False
        if limit <= 0:
            return False
        return self._store.count_since(kind, self._day_start()) < limit

    def _cooldown_ready(self, *, exclude_action_key: str = "") -> bool:
        if self._store is None:
            return False
        # 点赞不占评论和回复的发送冷却。
        previous = self._store.last_outgoing_at(
            exclude_action_key=exclude_action_key,
            exclude_kinds=("post_like",),
        )
        if previous is None:
            return True
        return time.time() - previous >= max(0, int(self.config.send_cooldown_seconds))

    async def _dispatch_comment(self, client, *, action_key: str, route: RoutingTarget, content: str) -> None:
        assert self._store is not None
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            return
        if not self._cooldown_ready(exclude_action_key=action_key):
            self._store.update_outgoing(action_key, OutgoingStatus.SUPPRESSED, error="cooldown")
            return
        # Persist the send boundary before touching the external API. If the
        # process dies after this point, reconciliation may read back the
        # thread, but it must never blindly issue the same write again.
        self._store.update_outgoing(action_key, OutgoingStatus.SENDING, error="send_started")
        try:
            result = await client.send_comment(route, content)
            if not result.confirmed:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN)
                return
            verify_route = route
            if not route.root_comment_id and result.external_comment_id:
                # The post-tree endpoint exposes a root-specific comment under
                # current_comment. Use the returned main-comment ID for
                # readback instead of querying the post's empty top-level list.
                verify_route = RoutingTarget(
                    profile_id=str(getattr(route, "profile_id", self.config.profile_id)),
                    post_id=route.post_id,
                    root_comment_id=str(result.external_comment_id),
                    parent_comment_id=str(result.external_comment_id),
                    notification_id=str(getattr(route, "notification_id", "")),
                )
            verified = await self._verify_comment(
                client,
                verify_route,
                content,
                external_comment_id=result.external_comment_id,
            )
            status = OutgoingStatus.VERIFIED if verified else OutgoingStatus.SEND_UNKNOWN
            self._store.update_outgoing(
                action_key,
                status,
                external_id=result.external_comment_id,
                error="" if verified else "readback_not_found",
            )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            status = OutgoingStatus.FAILED if category in {
                "credential_invalid",
                "forbidden",
                "rate_limited",
                "upstream_rejected",
                "response_shape",
                "http",
            } else OutgoingStatus.SEND_UNKNOWN
            detail = getattr(exc, "details", {})
            detail_keys = sorted(str(key) for key in detail) if isinstance(detail, dict) else []
            self._store.update_outgoing(
                action_key,
                status,
                error=_error_label(exc),
            )
            self.ctx.logger.error(
                "小黑盒评论发送失败或结果未知：type=%s category=%s status=%s message=%s detail_keys=%s",
                type(exc).__name__,
                category,
                getattr(exc, "status_code", ""),
                str(exc)[:240],
                detail_keys,
            )

    async def _verify_comment(
        self,
        client,
        route: RoutingTarget,
        content: str,
        *,
        external_comment_id: str = "",
    ) -> bool:
        expected = _squash_text(content)
        if not expected:
            return False
        candidates: list[dict[str, object]] = []
        try:
            # The thread tree is required for content-aware replies anyway.
            # Flatten nested sub-comments so floor replies (楼中楼) can match.
            context = await client.fetch_thread_context(
                route.post_id,
                root_comment_id=route.root_comment_id,
            )
            candidates.extend(_iter_comment_nodes(getattr(context, "comments", []) or []))
        except Exception as exc:  # noqa: BLE001
            self._log_debug("小黑盒评论读回核验失败：%s", _error_label(exc))
        if _comment_matches(candidates, expected, external_comment_id):
            return True
        # Fallback: the account's own recent comments list, which also covers
        # comments hidden from the public thread tree (e.g. pending ads/review).
        recent = getattr(client, "recent_comments", None)
        if callable(recent):
            try:
                items = await recent(route, limit=30)
                candidates.extend(
                    _iter_comment_nodes(items if isinstance(items, list) else [])
                )
            except Exception as exc:  # noqa: BLE001
                self._log_debug("小黑盒近期评论兜底核验失败：%s", _error_label(exc))
        return _comment_matches(candidates, expected, external_comment_id)

    async def _reconcile_unknowns(self, client) -> None:
        if self._store is None:
            return
        rows: list[dict[str, object]] = []
        for status in (OutgoingStatus.SENDING, OutgoingStatus.SEND_UNKNOWN):
            rows.extend(self._store.list_outgoing(status=status, limit=30))
        for row in rows:
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            try:
                if row.get("kind") in {"notification_reply", "proactive_comment"}:
                    route = RoutingTarget(
                        profile_id=self.config.profile_id,
                        post_id=str(metadata.get("post_id") or row.get("target_id") or ""),
                        root_comment_id=str(metadata.get("root_comment_id") or ""),
                        parent_comment_id=str(metadata.get("parent_comment_id") or ""),
                    )
                    if route.post_id and await self._verify_comment(
                        client,
                        route,
                        str(row.get("content") or ""),
                        external_comment_id=str(row.get("external_id") or ""),
                    ):
                        self._store.update_outgoing(row["action_key"], OutgoingStatus.VERIFIED)
                elif row.get("kind") == "post_like":
                    # 点赞中途重启：点赞可安全重做，记 failed 让以后的浏览重新判断。
                    self._store.update_outgoing(row["action_key"], OutgoingStatus.FAILED, error="interrupted")
                elif row.get("kind") == "proactive_post":
                    link_id = str(
                        metadata.get("link_id")
                        or row.get("target_id")
                        or row.get("external_id")
                        or ""
                    )
                    if link_id:
                        context = await client.fetch_thread_context(link_id)
                        if context and _same_post_content(
                            str(metadata.get("title") or ""),
                            str(metadata.get("body") or row.get("content") or ""),
                            context,
                            require_image=bool(metadata.get("image_required")),
                        ):
                            self._store.update_outgoing(row["action_key"], OutgoingStatus.VERIFIED, external_id=link_id)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug("小黑盒未知发送状态仍无法核验：%s", type(exc).__name__)

    async def _proactive_once(
        self,
        client,
        *,
        post_window_active: bool | None = None,
        browse_window_active: bool | None = None,
    ) -> None:
        if not self.config.enabled or str(self.config.mode).lower() not in {"auto", "dry_run"}:
            return
        if post_window_active is None:
            post_window_active = _time_window_active(
                self.config.post_active_start_time,
                self.config.post_active_end_time,
            )
        if browse_window_active is None:
            browse_window_active = _time_window_active(
                self.config.feed_active_start_time,
                self.config.feed_active_end_time,
            )
        if not (post_window_active or browse_window_active):
            return
        if await self._persona_asleep():
            self.ctx.logger.info("小黑盒本次不逛：生活状态显示她在睡觉或小睡")
            return
        if (
            post_window_active
            and not self.config.summary_enabled
            and self._within_quota("proactive_post", int(self.config.posts_per_day))
        ):
            await self._create_proactive_post(client)
        if browse_window_active:
            await self._browse_session(client)

    def _next_feed_gap(self) -> float:
        """下次逛推荐流的间隔：配置值的 0.5–1.5 倍，避免每天同一时刻出现。"""

        base = max(300, int(self.config.feed_interval_seconds))
        return base * self._rng.uniform(0.5, 1.5)

    async def _persona_asleep(self) -> bool:
        """生活频率显示她在睡觉或小睡时不刷小黑盒；读不到生活状态时按醒着处理。"""

        if self._persona is None:
            return False
        try:
            text = await self._persona.life_context()
        except Exception:  # noqa: BLE001
            return False
        return bool(_ASLEEP_PATTERN.search(str(text or "")))

    def _daily_chance(self, kind: str, limit: int, seconds_left: float, *, cap: float = 1.0) -> float:
        """把当天剩余额度摊到剩余的浏览场次上，评论和点赞不会集中在上午用完。"""

        if self._store is None or limit <= 0:
            return 0.0
        remaining = limit - self._store.count_since(kind, self._day_start())
        if remaining <= 0:
            return 0.0
        sessions_left = max(1.0, seconds_left / max(300, int(self.config.feed_interval_seconds)))
        # 一页推荐流里通常只有少量帖子命中浏览范围，所以按每场一次机会估算。
        return max(0.0, min(cap, remaining / sessions_left))

    def _feed_candidate(self, item: object) -> bool:
        if not isinstance(item, dict):
            return False
        post_id = str(item.get("post_id") or item.get("link_id") or "")
        if not post_id:
            return False
        if self._is_own_feed_item(item):
            return False
        allowlist_values = _normalize_selection_values(
            self.config.community_allowlist,
            max_items=MAX_ALLOWLIST_ITEMS,
        )
        allowlist = {_normalize_selection_name(value) for value in allowlist_values}
        if not allowlist:
            return False
        values = {str(item.get(key) or "") for key in ("community_id", "topic_id", "section_id", "link_tag")}
        values.update(str(value) for value in item.get("section_names", []) if value)
        if not {_normalize_selection_name(value) for value in values}.intersection(allowlist):
            return False
        text = " ".join(
            str(item.get(key) or "") for key in ("title", "subject", "content", "description", "text")
        )
        text += " " + " ".join(str(value) for value in item.get("section_names", []) if value)
        lowered = text.casefold()
        hints = [str(value).casefold() for value in self.config.game_topic_hints if str(value).strip()]
        if hints and not any(hint in lowered for hint in hints):
            return False
        return is_publishable(text)[0]

    def _is_own_feed_item(self, item: dict[str, object]) -> bool:
        if not (self._runtime and self._runtime.credentials):
            return False
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        return str(author.get("uid") or author.get("userid") or "") == str(self._runtime.credentials.uid)

    def _feed_readable(self, item: object) -> bool:
        """可以点开看的帖子：有帖子 ID、不是自己发的。

        只是点开看、不发任何内容，所以比评论和点赞的筛选宽；评论和点赞仍走 _feed_candidate。
        """

        if not isinstance(item, dict):
            return False
        if not str(item.get("post_id") or item.get("link_id") or ""):
            return False
        return not self._is_own_feed_item(item)

    def _pick_posts_to_read(self, items: list[object]) -> list[dict[str, object]]:
        """按推荐流顺序挑 2–4 篇点开：命中浏览范围的必看，其余帖子随缘点开。"""

        target = self._rng.randint(2, 4)
        picked: list[dict[str, object]] = []
        for item in items:
            if len(picked) >= target:
                break
            if not self._feed_readable(item):
                continue
            if self._feed_candidate(item) or self._rng.random() < 0.35:
                picked.append(item)  # type: ignore[arg-type]
        return picked

    async def _pause(self, low: float, high: float) -> None:
        await asyncio.sleep(self._rng.uniform(low, high))

    async def _comment_on_feed_item(self, client, item: dict[str, object], context=None) -> bool:
        """返回是否真的发出了评论（模型给 SKIP 或内容不合规时返回 False）。"""

        assert self._store is not None and self._generator is not None
        post_id = str(item.get("post_id") or item.get("link_id") or "")
        if context is None:
            context = await client.fetch_thread_context(post_id)
        if context is None:
            return False
        reply = await self._generator.reply(
            post_title=str(getattr(context, "title", "")),
            post_body=str(getattr(context, "body", "")),
            proactive=True,
        )
        reply = normalize_outgoing_text(reply) if reply else reply
        if not reply or not is_publishable(reply)[0]:
            return False
        action_key = f"feed_comment:{post_id}:{content_fingerprint(reply)}"
        action = OutgoingAction(
            action_key,
            "proactive_comment",
            post_id,
            reply,
            metadata={"post_id": post_id},
        )
        if not self._store.begin_outgoing(action):
            return False
        await self._dispatch_comment(
            client,
            action_key=action_key,
            route=RoutingTarget(profile_id=self.config.profile_id, post_id=post_id),
            content=reply,
        )
        return True

    @staticmethod
    def _feed_community(item: dict[str, object]) -> str:
        names = item.get("section_names")
        if isinstance(names, list):
            for value in names:
                text = str(value or "").strip()
                if text:
                    return text[:40]
        for key in ("community_name", "topic_name", "section_name"):
            text = str(item.get(key) or "").strip()
            if text:
                return text[:40]
        return ""

    def _record_seen_post(self, item: dict[str, object], context: object) -> None:
        """Remember what the account just read, for the daily summary post."""

        if self._store is None:
            return
        post_id = str(item.get("post_id") or item.get("link_id") or "")
        title = str(
            getattr(context, "title", "") or item.get("title") or item.get("subject") or ""
        )
        body = str(
            getattr(context, "body", "") or item.get("content") or item.get("description") or ""
        )
        if not post_id or not normalize_text(title):
            return
        safe_title = normalize_text(title, 100)
        safe_excerpt = normalize_text(body, 160)
        community = self._feed_community(item)
        self._store.record_seen_post(
            post_id,
            title=safe_title,
            excerpt=safe_excerpt,
            community=community,
        )
        self._store.record_digest_material(
            post_id,
            title=safe_title,
            excerpt=safe_excerpt,
            community=community,
        )

    async def _seen_posts_for_post(self, client) -> list[dict[str, str]]:
        """Today's opened posts; top up from the feed when there are too few."""

        if self._store is None or not self.config.post_summarize_feed:
            return []
        seen = self._store.list_seen_posts_since(self._day_start(), limit=6)
        if len(seen) >= 2:
            return seen
        fetch_feed = getattr(client, "fetch_feed", None)
        if fetch_feed is None:
            return seen
        try:
            page = await fetch_feed(offset=0)
        except Exception as exc:  # noqa: BLE001
            self._log_debug("发帖前补拉推荐流失败：%s", type(exc).__name__)
            return seen
        merged = list(seen)
        known = {str(item.get("post_id") or "") for item in seen}
        for item in page.items:
            if len(merged) >= 6:
                break
            if not isinstance(item, dict) or self._is_own_feed_item(item):
                continue
            post_id = str(item.get("post_id") or item.get("link_id") or "")
            title = normalize_text(str(item.get("title") or item.get("subject") or ""), 80)
            if not post_id or not title or post_id in known:
                continue
            merged.append(
                {
                    "post_id": post_id,
                    "title": title,
                    "excerpt": normalize_text(
                        str(item.get("content") or item.get("description") or ""), 120
                    ),
                    "community": self._feed_community(item),
                    "seen_at": time.time(),
                }
            )
            known.add(post_id)
        return merged

    async def _browse_session(self, client) -> None:
        """像人一样逛一次推荐流：点开几篇看，大多只看不评，偶尔点赞，最多评论一条。"""

        try:
            page = await client.fetch_feed(offset=0)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒推荐流读取失败：%s", type(exc).__name__)
            return
        picked = self._pick_posts_to_read(list(page.items))
        seconds_left = _window_seconds_left(
            self.config.feed_active_start_time,
            self.config.feed_active_end_time,
        )
        opened = commented = liked = 0
        for item in picked:
            post_id = str(item.get("post_id") or item.get("link_id") or "")
            try:
                context = await client.fetch_thread_context(post_id)
            except Exception as exc:  # noqa: BLE001
                self.ctx.logger.debug("小黑盒帖子打开失败：%s", type(exc).__name__)
                continue
            opened += 1
            self._record_seen_post(item, context)
            await self._pause(8, 40)
            if not self._feed_candidate(item):
                continue
            did_comment = False
            comment_chance = self._daily_chance(
                "proactive_comment", int(self.config.proactive_comments_per_day), seconds_left
            )
            if not commented and self._rng.random() < comment_chance:
                did_comment = await self._comment_on_feed_item(client, item, context)
                commented += int(did_comment)
            like_chance = self._daily_chance(
                "post_like", int(self.config.likes_per_day), seconds_left, cap=0.7
            )
            if did_comment and like_chance > 0:
                like_chance = max(like_chance, 0.5)
            if self._rng.random() < like_chance and await self._like_feed_item(client, post_id):
                liked += 1
        self.ctx.logger.info(
            "小黑盒逛了一次推荐流：点开 %s 篇，评论 %s 条，点赞 %s 个", opened, commented, liked
        )

    async def _like_feed_item(self, client, post_id: str) -> bool:
        """给帖子点赞；同一帖子只记一次，返回是否新做了这次点赞。"""

        assert self._store is not None
        action_key = f"post_like:{post_id}"
        action = OutgoingAction(action_key, "post_like", post_id, "", metadata={"post_id": post_id})
        if not self._store.begin_outgoing(action):
            return False
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            return True
        self._store.update_outgoing(action_key, OutgoingStatus.SENDING, error="send_started")
        try:
            await client.like_post(post_id)
        except Exception as exc:  # noqa: BLE001
            # award_type=1 是「设为已赞」，以后重试不会重复计数，所以失败直接记 failed。
            self._store.update_outgoing(action_key, OutgoingStatus.FAILED, error=_error_label(exc))
            self.ctx.logger.warning("小黑盒点赞失败：%s", _error_label(exc))
            return False
        self._store.update_outgoing(action_key, OutgoingStatus.VERIFIED)
        return True

    async def _create_proactive_post(self, client, *, ignore_window: bool = False) -> None:
        if self._store is None or self._generator is None or self._persona is None:
            return
        if not ignore_window and not _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        ):
            return
        raw_targets = _normalize_selection_values(self.config.post_topic_ids, max_items=None)
        targets = raw_targets[:MAX_POST_TARGETS]
        if not targets:
            return
        if len(raw_targets) > MAX_POST_TARGETS:
            self.ctx.logger.warning("小黑盒单条帖子最多选择 2 个分区，已使用前 2 个配置")
        try:
            topic_ids = await self._resolve_post_topic_ids(client, targets)
        except Exception as exc:  # noqa: BLE001
            self.ctx.logger.warning("小黑盒发帖目标解析失败：%s", type(exc).__name__)
            return
        if not topic_ids:
            return
        topic_id_value = ",".join(topic_ids)
        if not self._cooldown_ready():
            return
        seen_post_ids: list[str] = []
        retry_row = next(
            (
                row
                for row in self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=30)
                if row.get("kind") == "proactive_post"
                and not str(row.get("external_id") or "")
                and str(row.get("error") or "").startswith("pre_send:")
                and isinstance(row.get("metadata"), dict)
                and str(row["metadata"].get("title") or "").strip()
                and str(row["metadata"].get("body") or "").strip()
            ),
            None,
        )
        if retry_row is not None:
            metadata = retry_row["metadata"]
            title = str(metadata["title"])
            body = str(metadata["body"])
            fingerprint = str(metadata.get("fingerprint") or content_fingerprint(title + "\n" + body))
            action_key = str(retry_row["action_key"])
            self.ctx.logger.info("小黑盒主动帖准备重试同一条发送前失败动作：%s", action_key[:28])
        else:
            life_context = await self._persona.life_context()
            seen_posts = await self._seen_posts_for_post(client)
            seen_post_ids = [str(item.get("post_id") or "") for item in seen_posts]
            first_post = self._store.count_since("proactive_post", 0) == 0
            # 有今天刷到的帖子就优先总结；没有才用首帖自我介绍方向。
            topic_hint = (
                "刚来小黑盒，轻松介绍一下自己，语气自然像刚来报到"
                if first_post and not seen_posts
                else ""
            )
            draft = await self._generator.post(
                life_context=life_context,
                topic_hint=topic_hint,
                seen_posts=seen_posts,
            )
            if not draft:
                return
            title, body = draft
            title = normalize_outgoing_text(title)
            body = normalize_outgoing_text(body)
            if not is_publishable(title)[0] or not is_publishable(body)[0]:
                return
            fingerprint = content_fingerprint(title + "\n" + body)
            action_key = f"post:{fingerprint}"
        action = OutgoingAction(
            action_key,
            "proactive_post",
            "",
            body,
            request_id=f"xhh-post-{fingerprint[:40]}",
            metadata={
                "title": title,
                "body": body,
                "fingerprint": fingerprint,
                "topic_ids": topic_id_value,
                "image_required": bool(self.config.generate_post_images),
                "seen_post_ids": [item for item in seen_post_ids if item],
            },
        )
        if not self._store.begin_outgoing(action):
            return
        if str(self.config.mode).lower() == "dry_run":
            self._store.update_outgoing(action_key, OutgoingStatus.DRY_RUN)
            self.ctx.logger.info(
                "小黑盒主动发帖处于 dry_run，已记录草稿但未调用外部发帖接口：%s",
                action_key[:28],
            )
            return
        external_send_started = False
        link_id = ""
        stage = "prepare"
        try:
            blocks: list[dict[str, str]] = [{"type": "text", "text": body}]
            image_attached = False
            if self.config.generate_post_images:
                try:
                    image_bytes: bytes | None = None
                    # 优先：直连生图 API（自己的中转站或 MaiBot 已加载的画图模型）
                    if self._image_gen is not None:
                        stage = "direct_image"
                        prompt = f"maibot 的小黑盒生活分享配图：{title}。{body}。"
                        image_bytes = await self._image_gen.generate(prompt)
                    # 回退：麦麦绘图插件
                    if image_bytes is None and self._drawing is not None:
                        stage = "draw_submit"
                        reference = await self._persona.identity_reference()
                        prompt = f"maibot 的小黑盒生活分享配图：{title}。{body}。保持身份参考图中的人物形象一致。"
                        task = await self._drawing.submit(
                            prompt,
                            request_id=action.request_id,
                            source_images=[reference] if reference else None,
                        )
                        stage = "draw_task_id"
                        task_id = _task_id(task)
                        if not task_id:
                            raise RuntimeError("绘图任务未返回 task_id")
                        stage = "draw_wait"
                        finished = await self._drawing.wait(task_id, request_id=action.request_id)
                        if str(finished.get("status", "")).casefold() != "completed":
                            raise RuntimeError("绘图任务未完成")
                        stage = "draw_read_image"
                        image_bytes = await self._drawing.read_image(task_id)
                    if image_bytes is None:
                        raise RuntimeError("没有可用的生图方式")
                    stage = "upload_image"
                    uploaded = await client.upload_image_bytes(image_bytes, filename=f"{fingerprint}.png")
                    blocks.append(
                        {
                            "type": "img",
                            "url": str(uploaded["url"]),
                            "width": int(uploaded.get("width") or 0),
                            "height": int(uploaded.get("height") or 0),
                        }
                    )
                    image_attached = True
                except Exception as exc:  # noqa: BLE001
                    # Image generation is optional. A provider timeout or a
                    # stale task must not block the independent text-post
                    # path, and must not cause the same image task to be
                    # submitted again with a new request id.
                    fallback_reason = f"{stage}:{type(exc).__name__}"
                    self._store.merge_outgoing_metadata(
                        action_key,
                        {
                            "image_required": False,
                            "image_fallback": True,
                            "image_fallback_reason": fallback_reason,
                        },
                    )
                    self.ctx.logger.warning(
                        "小黑盒配图失败，已降级为纯文字发帖：stage=%s error=%s",
                        stage,
                        type(exc).__name__,
                    )

            stage = "create_post"
            external_send_started = True
            result = await client.create_post(
                title=title,
                content_blocks=blocks,
                topic_ids=topic_id_value,
                hashtags=self.config.post_hashtags,
            )
            link_id = str(result.get("link_id") or "")
            if not link_id:
                self._store.update_outgoing(action_key, OutgoingStatus.SEND_UNKNOWN, error="missing_link_id")
                return
            # Keep the id both in the row and in metadata: if the readback
            # below fails, reconciliation can still verify this post later.
            self._store.merge_outgoing_metadata(action_key, {"link_id": link_id})
            context = await client.fetch_thread_context(link_id)
            if context and _same_post_content(
                title,
                body,
                context,
                require_image=image_attached,
            ):
                self._store.update_outgoing(action_key, OutgoingStatus.VERIFIED, external_id=link_id)
            else:
                observed_title = normalize_text(str(getattr(context, "title", "")), 100)
                observed_body = normalize_text(str(getattr(context, "body", "")), 320)
                self.ctx.logger.warning(
                    "小黑盒发帖读回不匹配：link_id=%s observed_title=%r observed_body=%r expected_title=%r expected_body=%r",
                    link_id,
                    observed_title[:40],
                    observed_body[:60],
                    normalize_text(title, 40),
                    normalize_text(body, 60),
                )
                self._store.update_outgoing(
                    action_key,
                    OutgoingStatus.SEND_UNKNOWN,
                    external_id=link_id,
                    error="readback_not_found",
                )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            label = _error_label(exc)
            if link_id:
                # create_post already returned an id, so the post exists (or may
                # exist); only the readback failed. Keep the id for reconcile.
                status = OutgoingStatus.SEND_UNKNOWN
                error = f"readback:{stage}:{label}"
                self._store.update_outgoing(
                    action_key, status, external_id=link_id, error=error
                )
                self._store.merge_outgoing_metadata(action_key, {"link_id": link_id})
            elif not external_send_started:
                status = OutgoingStatus.FAILED
                error = f"pre_send:{stage}:{label}"
                self._store.update_outgoing(action_key, status, error=error)
            else:
                rejected = category in {
                    "upstream_rejected",
                    "credential_invalid",
                    "forbidden",
                    "rate_limited",
                    "http",
                    "response_shape",
                }
                status = OutgoingStatus.FAILED if rejected else OutgoingStatus.SEND_UNKNOWN
                error = f"create_post:{label}"
                self._store.update_outgoing(action_key, status, error=error)
            self.ctx.logger.error(
                "小黑盒主动发帖失败或结果未知：stage=%s label=%s category=%s status=%s detail=%s",
                stage,
                label,
                category,
                status,
                str(exc)[:180],
            )

    def _digest_topic(self) -> str:
        return str(self.config.automation.post_target_primary or "").strip()

    def _digest_status_snapshot(self) -> dict[str, object]:
        latest = self._store.latest_digest_run() if self._store is not None else None
        title = ""
        if latest:
            draft = _load_digest_draft(latest.get("draft_json"))
            if draft:
                title = draft[0]
        return {
            "pending": self._store.count_digest_pending() if self._store is not None else 0,
            "state": str((latest or {}).get("state") or ""),
            "title": title,
            "link_id": str((latest or {}).get("link_id") or ""),
            "error": str((latest or {}).get("error") or ""),
        }

    async def _maybe_publish_digest(self, client, *, force: bool = False) -> None:
        """Claim a batch of browsed posts and publish one summary post."""

        if self._store is None or self._generator is None or self._runtime is None:
            return
        latest = self._store.latest_digest_run()
        if latest and str(latest.get("state") or "") == "retry":
            await self._publish_digest(client, str(latest.get("id") or ""), latest)
            return
        batch = self._store.claim_digest(
            trigger=self.config.summary_trigger,
            every_posts=self.config.summary_every_posts,
            interval_hours=self.config.summary_interval_hours,
            topic=self._digest_topic(),
            force=force,
        )
        if not batch:
            return
        materials = self._store.digest_materials(batch)
        for item in materials:
            item["my_comments"] = self._store.digest_comment_materials(
                str(item.get("post_id") or "")
            )
        try:
            draft = await self._generator.digest(materials)
        except Exception as exc:  # noqa: BLE001
            self._store.update_digest_run(batch, "error", error=_error_label(exc))
            self.ctx.logger.warning("小黑盒总结帖生成失败：%s", _error_label(exc))
            return
        if not draft:
            self._store.update_digest_run(batch, "error", error="empty_generated_digest")
            return
        title, paragraphs = draft
        title = normalize_outgoing_text(title)
        paragraphs = [normalize_outgoing_text(paragraph) for paragraph in paragraphs]
        if (
            not title
            or len(paragraphs) < 2
            or not all(is_publishable(text)[0] for text in [title, *paragraphs])
        ):
            self._store.update_digest_run(batch, "failed", error="content_boundary")
            return
        draft_json = json.dumps(
            {"title": title, "paragraphs": paragraphs}, ensure_ascii=False
        )
        self._store.update_digest_run(batch, "ready", draft_json=draft_json)
        if str(self.config.mode).lower() == "dry_run":
            self.ctx.logger.info("小黑盒总结帖处于 dry_run：%s", title[:30])
            return
        await self._publish_digest(client, batch, {"draft_json": draft_json})

    async def _publish_digest(self, client, run_id: str, run: dict[str, object]) -> None:
        if self._store is None or not run_id:
            return
        draft = _load_digest_draft(run.get("draft_json"))
        if draft is None:
            self._store.update_digest_run(run_id, "error", error="draft_missing")
            return
        title, paragraphs = draft
        topic = self._digest_topic()
        if not topic:
            self._store.update_digest_run(run_id, "failed", error="no_topic")
            return
        try:
            topic_ids = await self._resolve_post_topic_ids(client, [topic])
        except Exception as exc:  # noqa: BLE001
            self._store.update_digest_run(run_id, "error", error=_error_label(exc))
            return
        if not topic_ids:
            self._store.update_digest_run(run_id, "failed", error="topic_unresolved")
            return
        self._store.update_digest_run(run_id, "sending")
        try:
            result = await client.create_post(
                title=title,
                content_blocks=[{"type": "text", "text": text} for text in paragraphs],
                topic_ids=",".join(topic_ids),
                hashtags=self.config.post_hashtags,
            )
            link_id = str(result.get("link_id") or "")
            if not link_id:
                self._store.update_digest_run(run_id, "unknown", error="missing_link_id")
                return
            context = await client.fetch_thread_context(link_id)
            if context and _same_post_content(title, "\n".join(paragraphs), context):
                self._store.update_digest_run(run_id, "sent", link_id=link_id)
                self.ctx.logger.info("小黑盒总结帖已发布：link_id=%s", link_id)
            else:
                self._store.update_digest_run(
                    run_id, "unknown", link_id=link_id, error="readback_not_found"
                )
        except Exception as exc:  # noqa: BLE001
            category = str(getattr(exc, "category", ""))
            rejected = category in {
                "upstream_rejected",
                "credential_invalid",
                "forbidden",
                "rate_limited",
                "http",
                "response_shape",
            }
            self._store.update_digest_run(
                run_id, "failed" if rejected else "unknown", error=_error_label(exc)
            )
            self.ctx.logger.error("小黑盒总结帖发布失败：%s", _error_label(exc))
    async def _resolve_post_topic_ids(self, client, targets: list[str]) -> list[str]:
        """Resolve friendly config options while retaining old numeric IDs."""

        numeric_ids = [item for item in targets if item.isdigit()]
        names = [item for item in targets if not item.isdigit()]
        if names:
            missing = [name for name in names if name not in self._topic_id_cache]
            if missing:
                self._topic_id_cache.update(await client.resolve_topic_names(missing))
            numeric_ids.extend(self._topic_id_cache.get(name, "") for name in names)
        return list(dict.fromkeys(item for item in numeric_ids if item.isdigit()))

    async def _retry_pre_send_proactive_posts(self, client) -> None:
        """Retry an action only when no external post request was started."""

        if self._store is None or not _time_window_active(
            self.config.post_active_start_time,
            self.config.post_active_end_time,
        ):
            return
        rows = self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=30)
        if any(
            row.get("kind") == "proactive_post"
            and not str(row.get("external_id") or "")
            and str(row.get("error") or "").startswith("pre_send:")
            for row in rows
        ):
            await self._create_proactive_post(client)

    @Tool("xiaoheihe_status", brief_description="查看小黑盒运营插件状态")
    async def handle_status(self, **kwargs):
        dm_summary = (
            self._dm_store.summary(self.config.profile_id)
            if self._dm_store is not None
            else {"available": False}
        )
        return {
            "success": True,
            "enabled": bool(self.config.enabled),
            "mode": str(self.config.mode),
            "profile_id": str(self.config.profile_id),
            "manual_cookie_configured": bool(str(self.config.manual_cookie or "").strip()),
            "reply_capabilities": {
                "post_comments": "已启用：读取原帖标题、正文和收到的评论后生成回复",
                "mentions_and_replies": "已启用：处理小黑盒通知中心的提及、评论和楼中楼",
                "direct_messages": (
                    "已启用：读取会话后按白名单自动回复私信"
                    if self.config.dm_enabled
                    else "未启用：开启「启用私信功能」并按白名单配置后生效"
                ),
            },
            "direct_message": {
                "enabled": bool(self.config.dm_enabled),
                "mode": str(self.config.dm_mode),
                "poll_interval_seconds": int(self.config.dm_poll_interval_seconds),
                "daily_quota": int(self.config.dm_daily_quota),
                "cooldown_seconds": int(self.config.dm_cooldown_seconds),
                "scope": str(self.config.dm_reply_scope),
                "allowlist_size": len(list(self.config.dm_user_allowlist or [])),
                "blocklist_size": len(list(self.config.dm_user_blocklist or [])),
                "images_enabled": bool(self.config.dm_generate_images),
                "paused_remaining_seconds": max(
                    0, int(self._dm_blocked_until - time.time())
                ),
                "state": dm_summary,
            },
            "summary": {
                "enabled": bool(self.config.summary_enabled),
                "trigger": str(self.config.summary_trigger),
                "every_posts": int(self.config.summary_every_posts),
                "interval_hours": int(self.config.summary_interval_hours),
                **self._digest_status_snapshot(),
            },
            "settings_help": {
                "mode": "dry_run 只记录计划；auto 才会真实发送",
                "feed_interval_seconds": "两次逛推荐流的平均间隔，实际在 0.5–1.5 倍之间随机；推荐 600 秒。每次点开 2–4 篇，多数只看不评",
                "likes_per_day": "每天最多点赞数；只赞命中浏览范围的帖子，同一帖子只赞一次",
                "feed_active_window": f"AI 浏览和主动评论只在服务器本地时间 {self.config.feed_active_start_time}-{self.config.feed_active_end_time} 运行；与发帖时间窗口独立",
                "post_active_window": f"主动发帖和发送前失败重试只在服务器本地时间 {self.config.post_active_start_time}-{self.config.post_active_end_time} 运行",
                "posts_per_day": "建议测试阶段保持 0",
                "notification_replies_per_day": "建议从 1 开始，确认链路后再增加",
                "dm_reply_scope": "仅白名单：只回复名单内 UID；除黑名单外全部：名单外的人都回复",
                "dm_user_allowlist": "「仅白名单」模式下生效；留空则只读不回复",
                "dm_user_blocklist": "黑名单里的 UID 永远不回复，优先级高于白名单",
                "dm_generate_images": "开启后私信回复会附带一张生成图，失败自动降级纯文字",
                "dm_mode": "私信默认 dry_run；确认链路后改成 auto 才会真正发送",
            },
            "message": "小黑盒运营插件已加载" if not self.config.enabled else "小黑盒运营插件运行中",
        }

    @Tool("xiaoheihe_request_login", brief_description="生成小黑盒扫码登录信息")
    async def handle_request_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            await self._runtime.request_qr()
            payload = self._webui_login_payload(
                {"state": "waiting_scan", "message": "二维码已生成，请使用小黑盒 App 扫码"}
            )
            return {"success": True, **payload}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool("xiaoheihe_check_login", brief_description="检查小黑盒扫码登录状态")
    async def handle_check_login(self, **kwargs):
        if self._runtime is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            result = await self._runtime.check_qr()
            safe_result = {
                key: result.get(key)
                for key in ("state", "message", "nickname")
                if key in result
            }
            payload = self._webui_login_payload(safe_result)
            return {"success": True, **payload}
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "error": type(exc).__name__}

    @Tool(
        "xiaoheihe_check_content",
        brief_description="检查一段内容是否符合 maibot 的小黑盒发布边界",
        parameters=(
            [
                ToolParameterInfo(
                    name="content",
                    param_type=ToolParamType.STRING,
                    description="需要检查的拟发布文本",
                    required=True,
                )
            ]
            if ToolParameterInfo is not None and ToolParamType is not None
            else None
        ),
    )
    async def handle_check_content(self, content: str, **kwargs):
        allowed, reason = is_publishable(content)
        return {"success": True, "allowed": allowed, "reason": reason}

    def _webui_login_payload(self, result: dict[str, object]) -> dict[str, object]:
        """Build a WebUI-safe login payload without exposing the raw QR URL."""

        if self._runtime is None:
            return {
                "state": "failed",
                "has_credentials": False,
                "message": "插件尚未加载",
                "qr_image": None,
                "remaining_seconds": 0,
            }
        session = self._runtime.session
        credentials = self._runtime.credentials
        payload = {
            "state": str(result.get("state") or ("success" if credentials else "idle")),
            "has_credentials": bool(credentials),
            "nickname": str(result.get("nickname") or (credentials.nickname if credentials else "")),
            "profile_id": self.config.profile_id,
            "message": str(result.get("message") or ""),
            "remaining_seconds": max(0, int(session.expires_at - time.time())) if session else 0,
            "qr_image": None,
        }
        if session is not None and session.qr_content and payload["state"] in {
            "waiting_scan",
            "scanned_waiting_confirm",
        }:
            payload["qr_image"] = qr_data_url(session.qr_content)
        return payload

    @API(
        name="webui_login_status",
        description="读取小黑盒 WebUI 登录状态并轮询当前二维码，只返回页面所需数据",
        version="1",
    )
    async def api_webui_login_status(self, **kwargs):
        del kwargs
        if self._runtime is None:
            return self._webui_login_payload({"state": "failed", "message": "插件尚未加载"})
        if self._runtime.session is not None:
            result = await self.handle_check_login()
        else:
            credentials = self._runtime.credentials
            result = {
                "state": "success" if credentials else "idle",
                "message": "已登录" if credentials else "尚未登录",
                "nickname": credentials.nickname if credentials else "",
            }
        return self._webui_login_payload(result)

    @API(
        name="webui_request_login",
        description="为小黑盒 WebUI 工作台生成新的扫码登录二维码",
        version="1",
    )
    async def api_webui_request_login(self, **kwargs):
        del kwargs
        result = await self.handle_request_login()
        if not result.get("success"):
            raise RuntimeError(str(result.get("error") or result.get("message") or "二维码生成失败"))
        return self._webui_login_payload({"state": "waiting_scan", "message": "请使用小黑盒 App 扫码"})

    @Command(
        "xhh_status",
        description="可在 MaiBot 聊天中输入 /xhh_status，查看小黑盒运营插件状态。",
        pattern=r"^/xhh_status$",
        permission="operator",
    )
    async def handle_command(self, **kwargs):
        result = await self.handle_status(**kwargs)
        return True, result["message"], 2

    @Command(
        "xhh_login",
        description="可在 MaiBot 聊天中输入 /xhh_login，生成小黑盒扫码登录信息。",
        pattern=r"^/xhh_login$",
        permission="operator",
    )
    async def handle_login_command(self, **kwargs):
        result = await self.handle_request_login(**kwargs)
        if not result.get("success"):
            return False, f"小黑盒登录二维码生成失败：{result.get('error') or result.get('message', 'unknown')}", 1
        return (
            True,
            "小黑盒登录二维码已生成，请打开 MaiBot 的‘小黑盒运营’插件工作台扫码；扫码后使用 /xhh_login_check 检查状态。",
            2,
        )

    @Command(
        "xhh_login_check",
        description="可在 MaiBot 聊天中输入 /xhh_login_check，检查扫码登录状态。",
        pattern=r"^/xhh_login_check$",
        permission="operator",
    )
    async def handle_login_check_command(self, **kwargs):
        result = await self.handle_check_login(**kwargs)
        return True, f"小黑盒登录状态：{result.get('state', 'unknown')} {result.get('message', '')}", 2


    # ── WebUI 仪表盘 ────────────────────────────────────────────

    @API(
        name="webui_dashboard",
        description="读取小黑盒运营仪表盘：账号、队列、今日额度、私信状态与最近动作",
        version="1",
    )
    async def api_webui_dashboard(self, **kwargs):
        del kwargs
        return self._dashboard_payload()

    @API(
        name="webui_check_now",
        description="立即检查通知与私信（不触发主动发帖或巡帖）",
        version="1",
    )
    async def api_webui_check_now(self, **kwargs):
        del kwargs
        if self._runtime is None:
            return self._dashboard_action("check_now", False, "插件尚未加载")
        if self._runtime.credentials is None:
            return self._dashboard_action("check_now", False, "小黑盒尚未登录")
        try:
            await self._check_inbox_once()
        except Exception as exc:  # noqa: BLE001
            return self._dashboard_action(
                "check_now", False, f"检查失败：{type(exc).__name__}"
            )
        return self._dashboard_action("check_now", True, "已检查通知与私信")

    @API(
        name="webui_retry_failed",
        description="把失败的评论回复放回重试队列",
        version="1",
    )
    async def api_webui_retry_failed(self, **kwargs):
        del kwargs
        count = self._requeue_failed_comments()
        message = f"已把 {count} 条失败评论放回重试队列" if count else "没有可重试的失败评论"
        return self._dashboard_action("retry_failed", True, message, {"count": count})

    def _dashboard_action(
        self, name: str, ok: bool, message: str, extra: dict[str, object] | None = None
    ) -> dict[str, object]:
        payload = self._dashboard_payload()
        payload["action_result"] = {
            "name": name,
            "ok": bool(ok),
            "message": message,
            **(extra or {}),
        }
        return payload

    def _dashboard_payload(self) -> dict[str, object]:
        """WebUI 用的只读快照：全部字段都是短字符串/数字，避免泄露原文。"""

        now = time.time()
        credentials = self._runtime.credentials if self._runtime is not None else None
        counters = {
            "planned": 0,
            "suppressed": 0,
            "sending": 0,
            "failed": 0,
            "send_unknown": 0,
            "verified": 0,
            "dry_run": 0,
        }
        recent: list[dict[str, object]] = []
        if self._store is not None:
            rows = self._store.list_outgoing(limit=200)
            for row in rows:
                status = str(row.get("status") or "")
                if status in counters:
                    counters[status] += 1
            for row in rows[:20]:
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                recent.append(
                    {
                        "action_key": str(row.get("action_key") or "")[:80],
                        "kind": str(row.get("kind") or "")[:32],
                        "status": str(row.get("status") or "")[:24],
                        "target": str(row.get("target_id") or "")[:40],
                        "post_id": str(metadata.get("post_id") or "")[:40],
                        "external_id": str(row.get("external_id") or "")[:40],
                        "error": str(row.get("error") or "")[:120],
                        "preview": normalize_text(str(row.get("content") or ""), 60),
                        "updated_at": int(float(row.get("updated_at") or 0)),
                    }
                )
        today = self._day_start()
        today_counts = {
            "posts": self._store.count_since("proactive_post", today) if self._store else 0,
            "comments": self._store.count_since("proactive_comment", today) if self._store else 0,
            "likes": self._store.count_since("post_like", today) if self._store else 0,
            "replies": self._store.count_since("notification_reply", today)
            if self._store
            else 0,
            "dm": self._store.count_since("dm_reply", today) if self._store else 0,
        }
        dm_summary: dict[str, object] = {}
        if self._dm_store is not None:
            summary = self._dm_store.summary(self.config.profile_id)
            dm_summary = {
                "message_count": int(summary.get("message_count") or 0),
                "baseline_ready": bool(summary.get("baseline_ready")),
            }
        return {
            "generated_at": int(now),
            "plugin": {
                "enabled": bool(self.config.enabled),
                "mode": str(self.config.mode),
                "profile_id": str(self.config.profile_id),
            },
            "account": {
                "logged_in": credentials is not None,
                "nickname": str(credentials.nickname if credentials else ""),
                "uid": str(credentials.uid if credentials else ""),
                "manual_cookie": bool(str(self.config.manual_cookie or "").strip()),
            },
            "counters": counters,
            "today": today_counts,
            "limits": {
                "posts_per_day": int(self.config.posts_per_day),
                "proactive_comments_per_day": int(self.config.proactive_comments_per_day),
                "likes_per_day": int(self.config.likes_per_day),
                "notification_replies_per_day": int(self.config.notification_replies_per_day),
                "dm_daily_quota": int(self.config.dm_daily_quota),
            },
            "direct_message": {
                "enabled": bool(self.config.dm_enabled),
                "mode": str(self.config.dm_mode),
                "scope": str(self.config.dm_reply_scope),
                "allowlist_size": len(list(self.config.dm_user_allowlist or [])),
                "blocklist_size": len(list(self.config.dm_user_blocklist or [])),
                "paused_remaining_seconds": max(0, int(self._dm_blocked_until - now)),
                **dm_summary,
            },
            "summary": {
                "enabled": bool(self.config.summary_enabled),
                "trigger": str(self.config.summary_trigger),
                "every_posts": int(self.config.summary_every_posts),
                "interval_hours": int(self.config.summary_interval_hours),
                **self._digest_status_snapshot(),
            },
            "recent": recent,
        }

    async def _check_inbox_once(self) -> None:
        """Notifications + DMs only: what the dashboard button promises."""

        if self._store is None or self._runtime is None or self._runtime.credentials is None:
            return
        client = await self._runtime.client()
        await self._reconcile_unknowns(client)
        await self._retry_suppressed_comments(client)
        for event_type in (NotificationType.MENTION, NotificationType.REPLY):
            await self._poll_notifications(client, event_type)
        if self.config.dm_enabled:
            self._last_dm_poll_at = time.time()
            await self._poll_direct_messages(client)

    @Command(
        "xhh_whoami",
        description="可在 MaiBot 聊天中输入 /xhh_whoami，查看当前平台与用户 ID（用于配置命令权限）。",
        pattern=r"^/xhh_whoami$",
    )
    async def handle_whoami_command(self, **kwargs):
        """权限诊断：不需要操作员权限，只回显自己的身份。"""

        platform = str(kwargs.get("platform") or "").strip()
        user_id = str(kwargs.get("user_id") or "").strip()
        session_id = str(
            kwargs.get("session_id") or kwargs.get("stream_id") or ""
        ).strip()
        scoped = f"{platform.lower()}:{user_id}" if platform and user_id else "(缺失)"
        host_permission: list[str] = []
        try:
            raw_permission = await self.ctx.config.get("plugin.permission", [])
        except Exception:  # noqa: BLE001
            raw_permission = []
        if isinstance(raw_permission, (list, tuple, set)):
            host_permission = [
                str(item).strip() for item in raw_permission if str(item).strip()
            ]
        matched = bool(scoped) and scoped in host_permission
        text = (
            f"platform={platform or '-'}\n"
            f"user_id={user_id or '-'}\n"
            f"platform:id={scoped}\n"
            f"session_id={session_id or '-'}\n"
            f"is_local_operator={bool(kwargs.get('is_local_operator'))}\n"
            f"host_permission={host_permission or '[]'}\n"
            f"matched={matched}\n"
            + (
                "已经命中操作员列表；如果命令仍被拒绝，请重启 MaiBot 或确认运行的是这份配置。"
                if matched
                else "把上面的 platform:id 原样写进 config/bot_config.toml 的 [plugin] permission，然后重启 MaiBot。"
            )
        )
        return True, text, 2

    @Command(
        "xhh_post_now",
        description="可在 MaiBot 聊天中输入 /xhh_post_now，立即尝试一次主动发帖（忽略时间窗，仍遵守 dry_run）。",
        pattern=r"^/xhh_post_now$",
        permission="operator",
    )
    async def handle_post_now_command(self, **kwargs):
        del kwargs
        if self._store is None or self._runtime is None or self._runtime.credentials is None:
            return False, "小黑盒尚未登录或插件未加载", 2
        try:
            client = await self._runtime.client()
            await self._create_proactive_post(client, ignore_window=True)
        except Exception as exc:  # noqa: BLE001
            return False, f"发帖失败：{_error_label(exc)}", 2
        rows = self._store.list_outgoing(limit=1)
        if not rows:
            return False, "没有产生发帖动作：检查发帖社区是否已选、冷却是否已过", 2
        row = rows[0]
        status = str(row.get("status") or "")
        if status == OutgoingStatus.VERIFIED.value:
            return True, f"发帖成功：link_id={row.get('external_id') or '-'}", 2
        if status == OutgoingStatus.DRY_RUN.value:
            return True, "dry_run：已生成草稿但未发送（切 auto 后会真的发）", 2
        link_id = str(row.get("external_id") or "")
        return False, f"发帖未确认：{status} | {row.get('error') or ''} | link_id={link_id or '-'}", 2

    @Command(
        "xhh_summary",
        description="可在 MaiBot 聊天中输入 /xhh_summary，查看浏览总结发帖状态。",
        pattern=r"^/xhh_summary$",
        permission="operator",
    )
    async def handle_summary_command(self, **kwargs):
        del kwargs
        if self._store is None:
            return False, "插件尚未加载", 2
        latest = self._store.latest_digest_run() or {}
        title = ""
        draft = _load_digest_draft(latest.get("draft_json")) if latest else None
        if draft:
            title = draft[0]
        text = (
            f"浏览总结：{'开启' if self.config.summary_enabled else '关闭'}\n"
            f"待总结素材：{self._store.count_digest_pending()} 帖\n"
            f"最近一次：{latest.get('state') or '-'}  {title or '-'}\n"
            f"link_id：{latest.get('link_id') or '-'}\n"
            f"错误：{latest.get('error') or '-'}"
        )
        return True, text, 2

    @Command(
        "xhh_summary_now",
        description="可在 MaiBot 聊天中输入 /xhh_summary_now，立即用当前素材生成并发布一篇总结。",
        pattern=r"^/xhh_summary_now$",
        permission="operator",
    )
    async def handle_summary_now_command(self, **kwargs):
        del kwargs
        if not self.config.summary_enabled:
            return False, "浏览总结未开启（配置里打开「浏览总结发帖」）", 2
        if self._runtime is None or self._runtime.credentials is None:
            return False, "小黑盒尚未登录或插件未加载", 2
        try:
            client = await self._runtime.client()
            await self._maybe_publish_digest(client, force=True)
        except Exception as exc:  # noqa: BLE001
            return False, f"总结失败：{_error_label(exc)}", 2
        latest = (self._store.latest_digest_run() or {}) if self._store else {}
        state = str(latest.get("state") or "")
        if state == "sent":
            return True, f"总结帖已发布：link_id={latest.get('link_id') or '-'}", 2
        if state == "ready":
            return True, "dry_run：总结草稿已生成，切 auto 后会发布", 2
        if not latest:
            return False, "没有可总结的素材，先让它逛几篇帖子", 2
        return False, f"总结未完成：{state} | {latest.get('error') or ''}", 2

    @Command(
        name="xhh_summary_retry",
        description="可在 MaiBot 聊天中输入 /xhh_summary_retry，重试失败/出错的总结任务。",
        pattern=r"^/xhh_summary_retry$",
        permission="operator",
    )
    async def handle_summary_retry_command(self, **kwargs):
        del kwargs
        if self._store is None:
            return False, "插件尚未加载", 2
        latest = self._store.latest_digest_run()
        if not latest:
            return False, "没有总结任务", 2
        state = str(latest.get("state") or "")
        if state not in {"error", "failed"}:
            return False, f"当前状态 {state} 不能重试（unknown 需先在小黑盒核对）", 2
        self._store.update_digest_run(str(latest["id"]), "retry")
        return True, "已放回重试队列，下一轮检查会重新发布已保存的草稿", 2

    @Command(
        name="xhh_summary_skip",
        description="可在 MaiBot 聊天中输入 /xhh_summary_skip，跳过当前总结任务。",
        pattern=r"^/xhh_summary_skip$",
        permission="operator",
    )
    async def handle_summary_skip_command(self, **kwargs):
        del kwargs
        if self._store is None:
            return False, "插件尚未加载", 2
        latest = self._store.latest_digest_run()
        if not latest:
            return False, "没有总结任务", 2
        state = str(latest.get("state") or "")
        if state not in {"error", "failed", "unknown", "retry"}:
            return False, f"当前状态 {state} 无需跳过", 2
        self._store.update_digest_run(str(latest["id"]), "discarded")
        return True, "已跳过本轮总结，素材会留到下一批", 2
    @Command(
        "xhh_help",
        description="可在 MaiBot 聊天中输入 /xhh_help，查看小黑盒插件的运维命令。",
        pattern=r"^/xhh_help$",
        permission="operator",
    )
    async def handle_help_command(self, **kwargs):
        del kwargs
        text = (
            "小黑盒运维命令：\n"
            "/xhh_status 查看状态\n"
            "/xhh_check 立即执行一轮检查\n"
            "/xhh_retry 把失败评论放回重试队列\n"
            "/xhh_clear_queue [帖子ID] 取消待发送评论\n"
            "/xhh_test 帖子ID 生成一条预览回复（不发送）\n"
            "/xhh_whoami 查看自己的 platform:id（配置命令权限用）\n"
            "/xhh_post_now 立即尝试一次主动发帖（忽略时间窗）\n"
            "/xhh_summary 查看浏览总结状态；/xhh_summary_now 立即总结一次\n"
            "/xhh_summary_retry 重试失败总结；/xhh_summary_skip 跳过本轮总结\n"
            "/xhh_login 生成扫码二维码；/xhh_login_check 检查登录"
        )
        return True, text, 2

    @Command(
        "xhh_check",
        description="可在 MaiBot 聊天中输入 /xhh_check，立即执行一轮通知、私信和巡帖检查。",
        pattern=r"^/xhh_check$",
        permission="operator",
    )
    async def handle_check_command(self, **kwargs):
        del kwargs
        if self._store is None or self._runtime is None or self._runtime.credentials is None:
            return False, "小黑盒尚未登录或插件未加载", 2
        try:
            await self._poll_once()
        except Exception as exc:  # noqa: BLE001
            return False, f"检查失败：{type(exc).__name__}", 2
        suppressed = len(
            self._store.list_outgoing(status=OutgoingStatus.SUPPRESSED, limit=100)
        )
        failed = len(self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=100))
        return True, f"已执行一轮检查；待重试 {suppressed} 条，失败 {failed} 条", 2

    @Command(
        "xhh_retry",
        description="可在 MaiBot 聊天中输入 /xhh_retry，把失败的评论回复放回重试队列。",
        pattern=r"^/xhh_retry$",
        permission="operator",
    )
    async def handle_retry_command(self, **kwargs):
        del kwargs
        if self._store is None:
            return False, "插件尚未加载", 2
        count = self._requeue_failed_comments()
        if count:
            return True, f"已把 {count} 条失败评论放回重试队列，下一轮检查会处理", 2
        return True, "没有可重试的失败评论", 2

    @Command(
        "xhh_clear_queue",
        description="可在 MaiBot 聊天中输入 /xhh_clear_queue [帖子ID]，取消尚未发送的评论回复。",
        pattern=r"^/xhh_clear_queue(?:\s+(?P<post_id>\S+))?\s*$",
        permission="operator",
    )
    async def handle_clear_queue_command(self, **kwargs):
        if self._store is None:
            return False, "插件尚未加载", 2
        matched = kwargs.get("matched_groups") or {}
        post_id = str(matched.get("post_id") or "").strip()
        count = self._cancel_pending_comments(post_id)
        scope = f"帖子 {post_id} 下" if post_id else "全部"
        return True, f"已取消{scope} {count} 条待发送评论", 2

    @Command(
        "xhh_test",
        description="可在 MaiBot 聊天中输入 /xhh_test 帖子ID，读取帖子并生成预览回复但不发送。",
        pattern=r"^/xhh_test(?:\s+(?P<post_id>\S+))\s*$",
        permission="operator",
    )
    async def handle_test_command(self, **kwargs):
        matched = kwargs.get("matched_groups") or {}
        post_id = str(matched.get("post_id") or "").strip()
        if not post_id:
            return False, "用法：/xhh_test 帖子ID", 2
        preview = await self._preview_reply(post_id)
        if not preview.get("success"):
            return False, str(preview.get("message") or "预览失败"), 2
        title = str(preview.get("title") or "")
        draft = str(preview.get("draft") or "")
        reason = str(preview.get("reason") or "")
        check = "通过" if preview.get("allowed") else f"拦截（{reason}）"
        text = (
            f"帖子：{title}\n"
            f"预览回复：{draft}\n"
            f"内容检查：{check}\n"
            "（仅预览，未发送）"
        )
        return True, text, 2

    def _requeue_failed_comments(self, post_id: str = "") -> int:
        """Move retryable failed comment replies back to the suppressed queue."""

        if self._store is None:
            return 0
        requeued = 0
        for row in self._store.list_outgoing(status=OutgoingStatus.FAILED, limit=100):
            kind = str(row.get("kind") or "")
            if kind not in {"notification_reply", "proactive_comment"}:
                continue
            error = str(row.get("error") or "")
            if error.startswith("cancelled_by_user") or error.startswith("dm_"):
                continue
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            row_post = str(metadata.get("post_id") or row.get("target_id") or "")
            if post_id and row_post != post_id:
                continue
            self._store.update_outgoing(
                row["action_key"], OutgoingStatus.SUPPRESSED, error="manual_retry"
            )
            requeued += 1
        return requeued

    def _cancel_pending_comments(self, post_id: str = "") -> int:
        """Cancel planned/suppressed replies; SENDING rows are left alone."""

        if self._store is None:
            return 0
        cancelled = 0
        for status in (OutgoingStatus.PLANNED, OutgoingStatus.SUPPRESSED):
            for row in self._store.list_outgoing(status=status, limit=100):
                kind = str(row.get("kind") or "")
                if kind not in {"notification_reply", "proactive_comment"}:
                    continue
                metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
                row_post = str(metadata.get("post_id") or row.get("target_id") or "")
                if post_id and row_post != post_id:
                    continue
                self._store.update_outgoing(
                    row["action_key"], OutgoingStatus.FAILED, error="cancelled_by_user"
                )
                event_key = str(metadata.get("event_key") or "")
                if event_key:
                    self._store.mark_incoming_done(event_key, state="ignored")
                cancelled += 1
        return cancelled

    async def _preview_reply(self, post_id: str) -> dict[str, object]:
        """Read one post and generate a reply without sending or recording it."""

        if self._runtime is None or self._generator is None:
            return {"success": False, "message": "插件尚未加载"}
        try:
            client = await self._runtime.client()
            context = await client.fetch_thread_context(str(post_id))
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "message": f"读取帖子失败：{type(exc).__name__}"}
        if context is None:
            return {"success": False, "message": "帖子不存在或当前账号不可见"}
        title = str(getattr(context, "title", "") or "")
        body = str(getattr(context, "body", "") or "")
        draft = await self._generator.reply(
            post_title=title, post_body=body, proactive=True
        )
        draft = normalize_outgoing_text(draft) if draft else ""
        if not draft or draft.strip().upper() == "SKIP":
            return {"success": False, "message": "模型没有生成可用回复（可能输出了 SKIP）"}
        allowed, reason = is_publishable(draft)
        return {
            "success": True,
            "title": title,
            "draft": draft,
            "allowed": allowed,
            "reason": reason,
        }


class _StoredDirectMessage:
    """Attribute view over a dm_messages row so the reply path stays uniform."""

    __slots__ = (
        "message_id",
        "conversation_id",
        "peer_uid",
        "sender_uid",
        "sender_name",
        "text",
        "created_at",
    )

    def __init__(self, row: Mapping[str, Any]) -> None:
        self.message_id = str(row.get("message_id") or "")
        self.conversation_id = str(row.get("conversation_id") or "")
        self.peer_uid = str(row.get("peer_uid") or "")
        self.sender_uid = str(row.get("sender_uid") or "")
        self.sender_name = str(row.get("sender_name") or "")
        self.text = str(row.get("text") or "")
        self.created_at = float(row.get("created_at") or 0)


def _task_id(value: object) -> str:
    if isinstance(value, dict):
        for key in ("task_id", "id", "taskId"):
            item = value.get(key)
            if item not in (None, ""):
                return str(item)
        for item in value.values():
            found = _task_id(item)
            if found:
                return found
    return ""


def _normalize_selection_values(value: object, *, max_items: int | None) -> list[str]:
    result: list[str] = []
    raw_values = value if isinstance(value, list | tuple | set) else str(value or "").replace("，", ",").split(",")
    for item in raw_values:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
        if max_items is not None and len(result) >= max_items:
            break
    return result


def _normalize_selection_name(value: object) -> str:
    return "".join(str(value or "").split()).casefold()


def _normalize_topic_ids(value: object) -> list[str]:
    """Backward-compatible helper for old callers that supplied numeric IDs."""

    return [item for item in _normalize_selection_values(value, max_items=None) if item.isdigit()]


def _same_post_content(
    title: str,
    body: str,
    context: object,
    *,
    require_image: bool = False,
) -> bool:
    observed_title = normalize_text(str(getattr(context, "title", "")), 100)
    observed_body = normalize_text(str(getattr(context, "body", "")), 320)
    image_present = bool(getattr(context, "image_urls", []) or [])
    return bool(observed_title and observed_body) and (not require_image or image_present) and (
        content_fingerprint(observed_title) == content_fingerprint(normalize_text(title, 100))
        and content_fingerprint(observed_body) == content_fingerprint(normalize_text(body, 320))
    )


def _error_label(exc: BaseException) -> str:
    """Persist a useful, bounded error label without storing raw payloads."""

    category = str(getattr(exc, "category", "") or "").strip()
    status_code = str(getattr(exc, "status_code", "") or "").strip()
    message = normalize_text(str(exc), 160).strip()
    if not message or message.casefold() == type(exc).__name__.casefold():
        message = ""
    parts = [category or type(exc).__name__]
    if status_code and status_code != "0":
        parts.append(f"http_{status_code}")
    if message:
        parts.append(message)
    return ":".join(parts)[:500]


def _safe_status_data(value: object) -> str:
    """Keep probe diagnostics short and free of raw response payloads."""
    if not isinstance(value, dict):
        return normalize_text(str(value or ""), 300)
    allowed = {}
    for key in ("source", "accepted_count", "next_cursor", "has_more", "errors"):
        if key in value:
            allowed[key] = value[key]
    return normalize_text(str(allowed), 500)


def _comment_text(value: object) -> str:
    """Extract comment text from a thread-tree item without assuming one schema."""

    if not isinstance(value, dict):
        return str(value or "")
    for key in ("text", "content", "comment_a_text", "comment_text", "message"):
        candidate = value.get(key)
        if candidate not in (None, ""):
            return str(candidate)
    nested = value.get("comment")
    if isinstance(nested, dict):
        return _comment_text(nested)
    return ""


def _iter_comment_nodes(value: object, _seen: set[int] | None = None):
    """Yield every comment dict, including nested floor replies."""

    if _seen is None:
        _seen = set()
    if isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_comment_nodes(item, _seen)
        return
    if not isinstance(value, dict):
        return
    marker = id(value)
    if marker in _seen:
        return
    _seen.add(marker)
    yield value
    for key in (
        "comments",
        "comment_list",
        "children",
        "sub_comments",
        "subComments",
        "replies",
        "reply_list",
        "comment",
    ):
        child = value.get(key)
        if isinstance(child, (list, tuple, dict)):
            yield from _iter_comment_nodes(child, _seen)


def _squash_text(value: object) -> str:
    """Normalize whitespace so platform line-wrapping cannot break readback."""

    return "".join(str(value or "").split()).casefold()


def _comment_id(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    for key in ("commentid", "comment_id", "commentId", "id", "comment_a_id", "reply_id"):
        item = value.get(key)
        if item not in (None, ""):
            return str(item)
    return ""


def _comment_matches(
    candidates: list[dict[str, object]],
    expected: str,
    external_comment_id: str = "",
) -> bool:
    """Match by server id first, then by squashed text (tolerates @ prefixes)."""

    for item in candidates:
        if external_comment_id and _comment_id(item) == external_comment_id:
            return True
        text = _squash_text(_comment_text(item))
        if not text:
            continue
        if text == expected:
            return True
        if len(expected) >= 6 and (expected in text or text in expected):
            return True
    return False


def _load_digest_draft(value: object) -> tuple[str, list[str]] | None:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except json.JSONDecodeError:
            return None
    elif isinstance(value, dict):
        payload = value
    else:
        return None
    title = str(payload.get("title") or "").strip()
    paragraphs = [
        str(text).strip() for text in (payload.get("paragraphs") or []) if str(text).strip()
    ]
    if not title or len(paragraphs) < 2:
        return None
    return title, paragraphs


def create_plugin():
    return XiaoheiheOperatorPlugin()

