"""小黑盒表情标识符规范化。

模型经常把表情写成 ``[cube_狗头]`` 这类中文别名，小黑盒只认标准标识符
（例如 ``[cube_doge]``），发出去会被判定为非法表情甚至整条评论被拒。
这里做一层保守映射：

- 已知别名 / 标准名 → 标准标识符；
- ``cube_`` / ``bigemoji_`` 命名空间里不认识的 → 直接删掉（宁可不发表情）；
- 其它方括号内容（``[链接_1]`` 等）原样保留，避免误删正文。

离线表只覆盖常用表情；完整列表以后可接 ``fetch_emojis`` 动态刷新。
"""

from __future__ import annotations

import re

STANDARD_EMOJI_NAMES: frozenset[str] = frozenset(
    {
        "bigemoji_？",
        "bigemoji_暗中观察",
        "bigemoji_比心",
        "bigemoji_吃瓜",
        "bigemoji_打折",
        "bigemoji_委屈",
        "bigemoji_哈哈",
        "bigemoji_滑稽",
        "bigemoji_开可乐",
        "bigemoji_厉害",
        "bigemoji_求带",
        "bigemoji_摔",
        "bigemoji_喜加一",
        "bigemoji_羡慕",
        "bigemoji_做梦",
        "cube_+1",
        "cube_-1",
        "cube_2023",
        "cube_2024",
        "cube_2025",
        "cube_2026",
        "cube_doge",
        "cube_H币",
        "cube_P的谎言",
        "cube_wota",
        "cube_爱心",
        "cube_比心",
        "cube_比耶",
        "cube_闭嘴",
        "cube_并不简单",
        "cube_菜doge",
        "cube_沧桑",
        "cube_超人",
        "cube_吃瓜",
        "cube_吃鸡啦",
        "cube_吹口哨",
        "cube_打脸",
        "cube_打咩",
        "cube_蛋糕",
        "cube_点赞",
        "cube_电牛",
        "cube_鹅",
        "cube_感动",
        "cube_咕咕",
        "cube_鼓掌",
        "cube_乖",
        "cube_害羞",
        "cube_汗",
        "cube_盒十",
        "cube_黑人问号",
        "cube_红包",
        "cube_滑稽",
        "cube_加油",
        "cube_僵尸",
        "cube_惊讶",
        "cube_开心",
        "cube_哭泣",
        "cube_酷",
        "cube_困",
        "cube_来财",
        "cube_良民",
        "cube_灵光一闪",
        "cube_玫瑰",
        "cube_猛男微笑",
        "cube_摸摸头",
        "cube_你懂我",
        "cube_怒",
        "cube_柠檬",
        "cube_呕吐",
        "cube_佩服",
        "cube_喷水",
        "cube_期待",
        "cube_求抱抱",
        "cube_委屈",
        "cube_笑哭",
        "cube_嫌弃",
        "cube_赞",
        "cube_震惊",
        "cube_抓狂",
    }
)

#: 常见中文/英文别名 → 标准标识符。键统一小写。
EMOJI_ALIASES: dict[str, str] = {
    "狗头": "cube_doge",
    "doge": "cube_doge",
    "神烦狗": "cube_doge",
    "滑稽": "cube_滑稽",
    "吃瓜": "cube_吃瓜",
    "点赞": "cube_点赞",
    "赞": "cube_赞",
    "大拇指": "cube_点赞",
    "比心": "cube_比心",
    "爱心": "cube_爱心",
    "心": "cube_爱心",
    "鼓掌": "cube_鼓掌",
    "加油": "cube_加油",
    "开心": "cube_开心",
    "笑": "cube_开心",
    "笑哭": "cube_笑哭",
    "哭": "cube_哭泣",
    "哭泣": "cube_哭泣",
    "怒": "cube_怒",
    "生气": "cube_怒",
    "困": "cube_困",
    "酷": "cube_酷",
    "害羞": "cube_害羞",
    "汗": "cube_汗",
    "惊讶": "cube_惊讶",
    "震惊": "cube_震惊",
    "乖": "cube_乖",
    "玫瑰": "cube_玫瑰",
    "花": "cube_玫瑰",
    "柠檬": "cube_柠檬",
    "红包": "cube_红包",
    "蛋糕": "cube_蛋糕",
    "你懂我": "cube_你懂我",
    "灵光一闪": "cube_灵光一闪",
    "黑人问号": "cube_黑人问号",
    "并不简单": "cube_并不简单",
    "打脸": "cube_打脸",
    "咕咕": "cube_咕咕",
    "摸摸头": "cube_摸摸头",
    "菜doge": "cube_菜doge",
    "鹅": "cube_鹅",
    "+1": "cube_+1",
    "加一": "cube_+1",
    "-1": "cube_-1",
    "2023": "cube_2023",
    "2024": "cube_2024",
    "2025": "cube_2025",
    "2026": "cube_2026",
    "暗中观察": "bigemoji_暗中观察",
    "求带": "bigemoji_求带",
    "喜加一": "bigemoji_喜加一",
}

_EMOJI_NAMESPACES = frozenset({"cube", "bigemoji"})
_EMOJI_PREFIX_ALIASES = frozenset({"表情", "emoji", "face"})
_EMOJI_TOKEN_RE = re.compile(r"\[([^\[\]\r\n]{1,40})\]")


def canonical_emoji_token(raw: str) -> str:
    """把方括号里的内容规范化成标准标识符；"" 表示应当丢弃。"""

    token = str(raw or "").strip()
    if not token:
        return ""
    for separator in (":", "："):
        if separator in token:
            prefix, _, name = token.partition(separator)
            if prefix.strip().casefold() in _EMOJI_PREFIX_ALIASES:
                return _resolve_name(name, "cube")
    if "_" in token:
        prefix, _, name = token.partition("_")
        namespace = prefix.strip().casefold()
        if namespace in _EMOJI_NAMESPACES:
            return _resolve_name(name, namespace)
        # 其它命名空间（链接、图片说明等）保持原样。
        return token
    alias = EMOJI_ALIASES.get(token.casefold())
    return alias or token


def _resolve_name(name: str, namespace: str) -> str:
    key = str(name or "").strip()
    if not key:
        return ""
    candidate = f"{namespace}_{key}"
    if candidate in STANDARD_EMOJI_NAMES:
        return candidate
    alias = EMOJI_ALIASES.get(key.casefold())
    if alias:
        return alias
    other = "bigemoji" if namespace == "cube" else "cube"
    alternative = f"{other}_{key}"
    if alternative in STANDARD_EMOJI_NAMES:
        return alternative
    return ""


def normalize_outgoing_text(value: str) -> str:
    """发送前把表情整理成平台认可的形式，并清掉多余空格。"""

    text = str(value or "")
    if not text:
        return ""

    def _replace(match: re.Match[str]) -> str:
        canonical = canonical_emoji_token(match.group(1))
        return f"[{canonical}]" if canonical else ""

    normalized = _EMOJI_TOKEN_RE.sub(_replace, text)
    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    return normalized.strip()
