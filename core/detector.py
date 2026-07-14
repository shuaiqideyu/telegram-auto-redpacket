"""红包识别与分类：把一条消息归类并提取完整识别上下文 + 领取载荷。

classify(msg) 返回 RedPacket，含三层信息：
1. 识别维度（全 kind 共用）：wallet 钱包 / state 状态 / conditions 条件 / 金额·剩余·发送者
2. kind 分类（领取方式）：direct / captcha / dm_captcha / webapp / fulilai / locked
3. 领取载荷：按 kind 取用（direct_data / start_param / captcha_buttons / fulilai_hash ...）

领取分类优先级：direct > captcha > fulilai > webapp(vweb--) > dm_captcha。

- direct     : 含关键词 callback 按钮 → 点一下即到账（最快）
- captcha    : 文本含算式 + callback 选项按钮 → 群内解题点按钮（窗口验证码）
- fulilai    : 按钮 URL 含 fllqb hash → hCaptcha token 池 + HTTP 领取（福利来红包）
- webapp     : start_param 以 vweb-- 开头 → /start → RequestWebView → 网页 AI 图片验证码
- dm_captcha : 其余 t.me?start= URL 按钮 → /start → bot 私聊出回执/解题（私信验证）
- locked     : 红包未解锁（含「解锁」按钮）→ 只通知不领取，等解锁后的编辑事件再领

状态机：detect_state 优先于领取分类。exhausted→None；locked→kind='locked'；
claimable→走领取分类。编辑事件（解锁/倒计时）会重复触发，靠 grabber 去重。
"""
import re
import unicodedata
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlparse

from telethon.tl.types import KeyboardButtonCallback, KeyboardButtonUrl

from .charset import MATH_RE as _MATH_RE
from .config import config

_ZERO_WIDTH = "\u200b\u200c\u200d\ufeff\u2060"

# ── 五类红包的唯一文案来源（grabber/runner 共用，前端 RecordsView 镜像保持一致）──
KIND_LABELS: dict[str, str] = {
    "direct": "关键词领取",
    "captcha": "窗口验证码",
    "dm_captcha": "私信验证",
    "webapp": "网页验证",
    "fulilai": "福利来红包",
    "locked": "未解锁红包",
}

# 真·网页验证（RequestWebView 图片验证码）的 bot 用户名白名单（小写）。
# 仅作回退：现在主要靠 start_param 前缀 `vweb--` 判定 webapp（见 classify）。
WEBAPP_BOTS: set[str] = {"okpay", "kkpay"}

# ── 钱包识别 ──────────────────────────────────────────────────────
# 新增钱包来源：只需在下面三张表各加一行（bot_id / username / 中文标签）。
WALLET_BOTS: dict[int, str] = {
    5703356189: "okpay",
    6505600437: "kkpay",
    8656187862: "wlqb",     # @HtjiamiBOT
    7826631206: "dlqb",
    5495837487: "fllqb",    # @fllqb 福利来
}

# bot username（小写）→ 钱包标识；用于无 via_bot/from 时从 URL 按钮兜底识别
WALLET_USERNAMES: dict[str, str] = {
    "okpay": "okpay",
    "kkpay": "kkpay",
    "htjiamibot": "wlqb",
    "dlqb": "dlqb",
    "fllqb": "fllqb",
}

# callback data 前缀 → 钱包（bot_id 识别失败时的回退；kkpay/dlqb 前缀相同，靠 bot_id 区分）
_CB_WALLET_PREFIXES: list[tuple[str, str]] = [
    ("redpacket--", "okpay"),
    ("receive_redpacket", "wlqb"),
    ("locked_redpacket", "wlqb"),
    ("mhblock", "fllqb"),
    ("rpi--", "kkpay"),
    ("rpg--", "kkpay"),
]

WALLET_LABELS: dict[str, str] = {
    "okpay": "OKPay钱包", "kkpay": "KKPay钱包", "wlqb": "未来钱包",
    "dlqb": "达利钱包", "fllqb": "福利来钱包", "unknown": "未知钱包",
}

CONDITION_LABELS: dict[str, str] = {
    "premium": "Premium会员", "group": "指定群组", "user": "指定用户",
    "turnover": "流水要求", "winloss": "输赢要求", "locked": "已锁定",
}

# ── 条件解析（只匹配中英文关键词，忽略 emoji 前缀——各钱包装饰不同）──────
# 只覆盖本项目 5 个钱包（okpay/kkpay/wlqb/dlqb/fllqb）实测存在的条件：
# premium / 指定群 / 指定用户 / 流水 / 输赢。红包监控.py 里的发言/充值/关注公众号
# 等是别家 bot 的玩法，本项目用不到，不引入以免误判。
_PREMIUM_RE = re.compile(r"仅限\s*Premium\s*会员")
_GROUP_RE = re.compile(r"仅限群组成员领取")
_GROUP_NAME_RE = re.compile(r"【\s*(.+?)\s*】")
_USER_RE = re.compile(r"专属红包")
_USER_NAME_RE = re.compile(r"专属红包[:：]\s*(.+?)(?:\n|$)")
_TURNOVER_RE = re.compile(r"流水要求|流水金额")
_TURNOVER_AMOUNT_RE = re.compile(r"流水金额[:：]\s*(.+?)(?:\n|$)")
_WINLOSS_RE = re.compile(r"输赢要求|输赢金额")
_WINLOSS_AMOUNT_RE = re.compile(r"输赢金额[:：]\s*(.+?)(?:\n|$)")
_LOCKED_TEXT_RE = re.compile(r"红包已锁定")

# ── 元数据解析 ──────────────────────────────────────────────────
# 金额+币种：币种只认 ASCII 字母（CNY/KKCOIN/WLCOIN/USDT），避免误吞「剩余」等中文
# total_count 直接取「剩余 x/N」的分母 N，不另写 共X个/有效期 等本项目钱包没有的字段。
_TOTAL_RE = re.compile(r"总金额[:：]?\s*([\d,]+(?:\.\d+)?)\s*([A-Za-z]+)?")
_REMAIN_RE = re.compile(r"剩余[:：]?\s*(\d+)\s*/\s*(\d+)")
_SENDER_RE = re.compile(r"🧧\s*(.+?)\s*发送了一个红包")

# ── 锁定状态：按钮文字含「解锁/已锁定」或 callback data 含以下标记 ──────
_LOCK_DATA_MARKERS = ("unlock_lucky", "unlock_redpacket", "locked_redpacket", "mhblock")


def _raw(msg) -> str:
    """优先用 raw_text（无 markdown 噪声），便于元数据/条件正则提取。"""
    return getattr(msg, "raw_text", None) or getattr(msg, "text", None) or ""


def _cb_data_str(btn) -> str:
    """callback 按钮 data → 小写字符串（非 callback 或无 data 返回 ''）。"""
    if not isinstance(btn, KeyboardButtonCallback) or not btn.data:
        return ""
    try:
        return btn.data.decode("utf-8", "ignore").lower()
    except Exception:
        return ""


def detect_wallet(msg) -> str:
    """识别红包来源钱包：via_bot/from → callback 前缀 → URL bot 名，三级回退。"""
    via = getattr(msg, "via_bot_id", None)
    if via and via in WALLET_BOTS:
        return WALLET_BOTS[via]
    from_id = getattr(getattr(msg, "from_id", None), "user_id", None)
    if from_id and from_id in WALLET_BOTS:
        return WALLET_BOTS[from_id]

    # callback data 前缀
    for row in _rows(msg):
        for btn in row.buttons:
            data = _cb_data_str(btn)
            if not data:
                continue
            for prefix, wallet in _CB_WALLET_PREFIXES:
                if data.startswith(prefix):
                    return wallet

    # URL 按钮的 bot username（仅匹配已知钱包，避免误把发包人主页当钱包）
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonUrl) or not btn.url:
                continue
            if "fllqb" in btn.url:
                return "fllqb"
            parsed = urlparse(btn.url)
            path = parsed.path.strip("/")
            bot = path.split("/")[0].lower() if path else ""
            if bot in WALLET_USERNAMES:
                return WALLET_USERNAMES[bot]
    return "unknown"


def parse_conditions(msg) -> list[str]:
    """解析领取条件（只靠关键词，忽略 emoji）。返回如
    ['premium', 'group:28圈 @er888', 'user:淋雨', 'turnover:1.0 U']。
    每项格式 'base' 或 'base:value'，base ∈ CONDITION_LABELS。"""
    t = _raw(msg)
    conds: list[str] = []
    if _PREMIUM_RE.search(t):
        conds.append("premium")
    if _GROUP_RE.search(t):
        m = _GROUP_NAME_RE.search(t)
        name = _clean_inline(m.group(1)) if m else ""
        conds.append(f"group:{name}" if name else "group")
    if _USER_RE.search(t):
        m = _USER_NAME_RE.search(t)
        name = _clean_inline(m.group(1)) if m else ""
        conds.append(f"user:{name}" if name else "user")
    if _TURNOVER_RE.search(t):
        m = _TURNOVER_AMOUNT_RE.search(t)
        val = _clean_inline(m.group(1)) if m else ""
        conds.append(f"turnover:{val}" if val else "turnover")
    if _WINLOSS_RE.search(t):
        m = _WINLOSS_AMOUNT_RE.search(t)
        val = _clean_inline(m.group(1)) if m else ""
        conds.append(f"winloss:{val}" if val else "winloss")
    return conds


def _clean_inline(s: str) -> str:
    """清理提取出的内联文本：去 markdown 星号、折叠空白。"""
    s = (s or "").replace("*", " ")
    return re.sub(r"\s+", " ", s).strip()


def detect_state(msg) -> str:
    """红包当前状态：exhausted（已领完）| locked（未解锁）| claimable（可领）。"""
    raw = _raw(msg)
    if _EXHAUSTED_RE.search(raw):
        return "exhausted"
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonCallback):
                continue
            label = clean(btn.text)
            if "解锁" in label or "已锁定" in label:
                return "locked"
            data = _cb_data_str(btn)
            if data and any(mk in data for mk in _LOCK_DATA_MARKERS):
                return "locked"
    return "claimable"


def parse_metadata(msg) -> dict:
    """提取金额/剩余份数/总数量/发送者昵称（与领取无关，纯展示用）。"""
    raw = _raw(msg)
    amount_text = remaining = sender = total_count = None
    m = _TOTAL_RE.search(raw)
    if m:
        num, unit = m.group(1), m.group(2)
        amount_text = f"{num} {unit}" if unit else num
    m = _REMAIN_RE.search(raw)
    if m:
        remaining = f"{m.group(1)}/{m.group(2)}"
        total_count = m.group(2)
    m = _SENDER_RE.search(raw)
    if m:
        sender = m.group(1).strip() or None
    return {"amount_text": amount_text, "remaining": remaining,
            "total_count": total_count, "sender_name": sender}


def clean(s: str) -> str:
    """剥离零宽字符/emoji/空格（按钮文字常插 \\u200d 等反爬），只留可见字符。"""
    out = []
    for ch in s or "":
        if ch in _ZERO_WIDTH:
            continue
        cat = unicodedata.category(ch)
        if cat.startswith("C") or cat == "So" or ch == " ":
            continue
        out.append(ch)
    return "".join(out)


def _rows(msg):
    return getattr(getattr(msg, "reply_markup", None), "rows", None) or []


_EXHAUSTED_RE = re.compile(r"剩余[:：]\s*0\s*/")


def is_red_packet(text: str) -> bool:
    """是否为红包消息（且仍有余额可领）。
    1) 命中可配关键词（OKPay 类：发送了一个红包 / 💵总金额）
    2) 结构识别：含算式 `X op Y = ?`——验证码红包文案千变万化
    3) 排除已领完：文本含「剩余:0/N」→ 已无余额，直接跳过"""
    t = text or ""
    # 已领完 → 不是可领红包
    if _EXHAUSTED_RE.search(t):
        return False
    if any(kw in t for kw in config.red_packet_keywords):
        return True
    if _MATH_RE.search(t):
        return True
    return False


# 非领取的通用 start 参数（bot 首页/帮助等导航链接），不应触发任何领取流程
_GENERIC_START = {"home", "start", "help", "menu", "settings", "about", "wallet"}


def extract_start(msg) -> tuple[str, str] | None:
    """提取领取相关的 URL 按钮 → (bot_username, start_param)。
    过滤掉通用导航参数（home/help/wallet 等），避免误把广告按钮当作领取入口。"""
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonUrl) or not btn.url:
                continue
            parsed = urlparse(btn.url)
            if "t.me" not in parsed.netloc and "t.me" not in parsed.path:
                continue
            start = parse_qs(parsed.query).get("start", [None])[0]
            if not start:
                continue
            if start.lower() in _GENERIC_START:
                continue
            # t.me/kkpay?start=xxx → bot_username = kkpay
            path = parsed.path.strip("/")
            bot = path.split("/")[-1] if path else ""
            return (bot, start)
    return None


def find_direct_claim(msg, keywords: list[str] | None = None) -> bytes | None:
    """含指定关键词的 callback 按钮 → 直接领取（子串匹配，兼容各类钱包的隐藏字符/emoji）。

    排除锁定按钮：fllqb 的「🧧解锁后领取，管理叫解锁是⚠️诈骗」含「领取」却是锁按钮
    （data=mhblock...），文字含「解锁/已锁定」或 data 命中锁标记一律跳过。"""
    kws = keywords or ["领取"]
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonCallback):
                continue
            text = clean(btn.text)
            if "解锁" in text or "已锁定" in text:
                continue
            data = _cb_data_str(btn)
            if data and (data.startswith(("mhblock", "unlock", "locked_"))
                         or any(mk in data for mk in _LOCK_DATA_MARKERS)):
                continue
            if any(kw in text for kw in kws):
                return btn.data
    return None


def _has_quiz_buttons(msg) -> bool:
    """是否有多个非领取 callback 按钮（验证码选项）。"""
    count = 0
    for row in _rows(msg):
        for btn in row.buttons:
            if isinstance(btn, KeyboardButtonCallback):
                label = clean(btn.text).strip()
                if label and "领取" not in label:
                    count += 1
    return count >= 2


def _collect_quiz_buttons(msg) -> list[tuple[str, bytes]]:
    buttons: list[tuple[str, bytes]] = []
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonCallback):
                continue
            label = clean(btn.text).strip()
            if label and "领取" not in label:
                buttons.append((label, btn.data))
    return buttons


def find_captcha_quiz(msg, decoded_text: str | None = None) -> tuple[str, list[tuple[str, bytes]]] | None:
    """验证码红包：文本含算式 + callback 按钮（数字或字母选项）。
    decoded_text: 已解码 custom emoji 后的文本（可选，未提供则用 raw_text）。
    返回 (算式文本, [(按钮标签, callback_data), ...]) 或 None。"""
    text = decoded_text or getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
    m = _MATH_RE.search(text)
    if not m:
        return None
    expr = m.group(0)
    buttons = _collect_quiz_buttons(msg)
    return (expr, buttons) if buttons else None


def find_fulilai(msg) -> tuple[str, str] | None:
    """福利来红包：按钮 URL 含 fllqb + startapp=... → 返回 (startapp 完整参数, 红包标识)。
    URL 格式有两代：
    - 旧: ...startapp=botId=xxx_page=captcha_hash=<hex>     → id_key='hash'
    - 新: ...startapp=botId=xxx_page=captcha_dataId=<digits> → id_key='dataId'
    返回 (startapp_raw, packet_id) 供 claimer 用。"""
    for row in _rows(msg):
        for btn in row.buttons:
            if not isinstance(btn, KeyboardButtonUrl) or not btn.url:
                continue
            if "fllqb" not in btn.url or "startapp=" not in btn.url:
                continue
            # 提取 startapp= 后面的整串参数
            m_sa = re.search(r"startapp=([^&\s]+)", btn.url)
            if not m_sa:
                continue
            startapp = m_sa.group(1)
            # 从 startapp 中提取红包标识（hash 或 dataId，取最后一个 key=value）
            for pattern in (r"hash=([a-f0-9]+)", r"dataId=(\d+)"):
                m_id = re.search(pattern, startapp)
                if m_id:
                    return (startapp, m_id.group(1))
    return None


@dataclass
class RedPacket:
    kind: str                          # 'direct'|'captcha'|'dm_captcha'|'webapp'|'fulilai'|'locked'
    # ── 识别维度（所有 kind 通用，供通知/汇总展示）──
    wallet: str = "unknown"            # 'okpay'|'kkpay'|'wlqb'|'dlqb'|'fllqb'|'unknown'
    state: str = "claimable"           # 'claimable'|'locked'|'exhausted'
    conditions: list[str] = field(default_factory=list)  # ['premium','group:28圈',...]
    amount_text: str | None = None     # "200 CNY"
    remaining: str | None = None       # "20/20"
    total_count: str | None = None     # "20"（总份数，取自剩余分母）
    sender_name: str | None = None     # "雨"
    # ── 领取载荷（按 kind 取用）──
    direct_data: bytes | None = None   # kind=direct
    start_param: str | None = None     # kind=webapp / dm_captcha
    webapp_bot: str | None = None      # kind=webapp / dm_captcha: bot username
    captcha_expr: str | None = None    # kind=captcha / dm_captcha: 算式原文
    captcha_buttons: list = field(default_factory=list)  # kind=captcha: [(label, data)]
    needs_emoji_decode: bool = False   # captcha 中含自定义 emoji，需要异步解码
    fulilai_hash: str | None = None    # kind=fulilai: 红包标识（hash 或 dataId）
    fulilai_startapp: str | None = None  # kind=fulilai: startapp 完整参数


def _expr_line(text: str) -> str | None:
    """提取表达式行：最后一个含 = ? 的行。"""
    for line in reversed(text.split("\n")):
        if "= ?" in line or "= ？" in line:
            return line.strip()
    return None


def _has_custom_emoji_in_expr(msg) -> bool:
    """表达式行内是否含 Custom Emoji（而非装饰性）。"""
    from telethon.tl.types import MessageEntityCustomEmoji
    raw = getattr(msg, "raw_text", None) or ""
    expr_line = _expr_line(raw)
    if not expr_line:
        return False
    entities = getattr(msg, "entities", None) or []
    # 定位表达式行在 raw_text 中的位置
    line_start = raw.rfind(expr_line)
    if line_start < 0:
        return False
    line_end = line_start + len(expr_line)
    # UTF-16 偏移量计算
    utf16_pos = 0
    char_to_utf16: list[int] = []
    for ch in raw:
        char_to_utf16.append(utf16_pos)
        utf16_pos += 2 if ord(ch) > 0xFFFF else 1
    start_u16 = char_to_utf16[line_start] if line_start < len(char_to_utf16) else utf16_pos
    end_u16 = char_to_utf16[line_end] if line_end < len(char_to_utf16) else utf16_pos
    return any(
        isinstance(e, MessageEntityCustomEmoji) and start_u16 <= e.offset < end_u16
        for e in entities
    )


def classify(msg, direct_keywords: list[str] | None = None, decoded_text: str | None = None) -> RedPacket | None:
    """把消息分类为红包并附带完整识别上下文。
    返回 None 仅当：非红包 / 已领完 / 无可识别按钮。
    locked（未解锁）会返回 kind='locked' 的 RedPacket（只通知不领取）。
    领取分类优先级：direct > captcha > fulilai > webapp(vweb--) > dm_captcha(其余 URL)。"""
    text = decoded_text or getattr(msg, "text", None) or getattr(msg, "raw_text", None) or ""
    if not getattr(msg, "reply_markup", None) or not is_red_packet(text):
        return None

    # 识别维度：钱包 / 状态 / 条件 / 元数据（与具体领取方式无关，全 kind 共用）
    wallet = detect_wallet(msg)
    state = detect_state(msg)
    conditions = parse_conditions(msg)
    meta = parse_metadata(msg)

    def _wrap(rp: RedPacket) -> RedPacket:
        rp.wallet = wallet
        rp.state = state
        rp.conditions = conditions
        rp.amount_text = meta["amount_text"]
        rp.remaining = meta["remaining"]
        rp.total_count = meta["total_count"]
        rp.sender_name = meta["sender_name"]
        return rp

    # 已领完 → 无事可做
    if state == "exhausted":
        return None

    # 未解锁 → 只通知不领取（等解锁后的编辑事件再领）
    if state == "locked":
        return _wrap(RedPacket("locked", state="locked"))

    # 1) 关键词领取（callback「领取」按钮，已排除锁按钮）
    data = find_direct_claim(msg, direct_keywords)
    if data:
        return _wrap(RedPacket("direct", direct_data=data))

    # 2) 窗口验证码（群内算式 + callback 选项按钮）
    quiz = find_captcha_quiz(msg, decoded_text)
    if quiz:
        from core.claimers.captcha import solve_expr
        expr, buttons = quiz
        if solve_expr(expr) is not None:
            return _wrap(RedPacket("captcha", captcha_expr=expr, captcha_buttons=buttons))
        # 解不出 → 检查表达式行是否含 Custom Emoji
        if not decoded_text and _has_custom_emoji_in_expr(msg):
            return _wrap(RedPacket("captcha", captcha_buttons=buttons, needs_emoji_decode=True))
        return _wrap(RedPacket("captcha", captcha_expr=expr, captcha_buttons=buttons))

    # 3) 福利来红包（fllqb URL，startapp + hash/dataId，与 ?start= 不同）
    fll = find_fulilai(msg)
    if fll:
        startapp, packet_id = fll
        return _wrap(RedPacket("fulilai", fulilai_hash=packet_id, fulilai_startapp=startapp))

    # 4) URL 按钮（t.me/bot?start=xxx）：start_param 以 vweb-- 开头即网页验证
    #    （okpay/kkpay/dlqb 共用 vweb 协议；不再硬编码 bot 白名单），其余按私信验证
    start_info = extract_start(msg)
    if start_info:
        bot, start = start_info
        expr_m = _MATH_RE.search(text)
        if start.startswith("vweb--") or bot.lower() in WEBAPP_BOTS:
            return _wrap(RedPacket("webapp", start_param=start, webapp_bot=bot))
        return _wrap(RedPacket("dm_captcha", start_param=start, webapp_bot=bot,
                               captcha_expr=expr_m.group(0) if expr_m else None))
    return None
