"""结构化报告：把红包识别上下文 + 领取结果拼成统一格式的通知文本。

四种输出（个人通知 + 频道三阶段广播），共用同一套钱包/条件/分割线样式：
  build_report          单账号领取详情（bot 私聊推给账号本人）
  build_detect_report   频道阶段1：检测到红包（钱包/金额/条件/分发数）
  build_locked_report   频道阶段2：未解锁红包（只通知，不领取）
  build_summary_report  频道阶段3：所有账号领完后的汇总（成功数/总额/逐账号）

各模式耗时口径不同：
  direct(关键词领取)   响应 · 领取
  captcha(窗口验证码)  响应 · 解题 · 领取
  dm_captcha(私信验证) 响应 · 私聊回执（含解题）
  webapp(网页验证)     响应 · 验证页 · 识别提交 · 到账
  fulilai(福利来)      响应 · 取token · 领取
"""
import re
import time
from collections import defaultdict
from dataclasses import dataclass, field

from .detector import CONDITION_LABELS, KIND_LABELS, WALLET_LABELS

SEP = "━ ━ ━ ━ ━"

# 失败归因 → 中文展示（汇总用）
ERROR_REASON_LABELS: dict[str, str] = {
    "exhausted": "已被领完",
    "blocked": "条件不满足",
    "wrong_captcha": "验证码识别错误",
    "premium": "需要会员",
}


def conditions_to_text(conditions: list[str]) -> str:
    """['premium','group:28圈 @er888'] → 'Premium会员 · 指定群组[28圈 @er888]'。"""
    parts: list[str] = []
    for c in conditions or []:
        base, _, val = c.partition(":")
        label = CONDITION_LABELS.get(base, base)
        parts.append(f"{label}[{val}]" if val else label)
    return " · ".join(parts)


def _amount_line(rp) -> str | None:
    """金额（剩余）行；无金额返回 None。"""
    amt = getattr(rp, "amount_text", None)
    if not amt:
        return None
    rem = getattr(rp, "remaining", None)
    return f"金额: {amt}（剩余 {rem}）" if rem else f"金额: {amt}"


def _info_lines(rp) -> list[str]:
    """红包公共信息块：金额/条件/发送者（缺省项不显示）。"""
    lines: list[str] = []
    amt = _amount_line(rp)
    if amt:
        lines.append(amt)
    if getattr(rp, "conditions", None):
        lines.append(f"条件: {conditions_to_text(rp.conditions)}")
    if getattr(rp, "sender_name", None):
        lines.append(f"发送者: {rp.sender_name}")
    return lines


def _format_timing(tm: dict) -> str:
    """按 mode 拼接耗时明细。"""
    def seg(a, b):
        if a in tm and b in tm:
            return f"{(tm[b] - tm[a]) * 1000:.0f}ms"
        return "—"

    gap = tm.get("detect", 0) - tm.get("msg_date", 0)
    resp = f"{gap * 1000:.0f}ms" if 0 <= gap <= 300 else "—"
    total = tm.get("done", time.time()) - tm.get("detect", 0)
    mode = tm.get("mode")

    if mode == "direct":
        return f"响应 {resp} · 领取 {seg('detect', 'submit')} · 共 {total:.2f}s"
    if mode == "captcha":
        return (f"响应 {resp} · 解题 {seg('detect', 'solve')} · "
                f"领取 {seg('solve', 'submit')} · 共 {total:.2f}s")
    if mode == "dm_captcha":
        return f"响应 {resp} · 私聊回执 {seg('start_sent', 'submit')} · 共 {total:.2f}s"
    if mode == "fulilai":
        return (f"响应 {resp} · 取token {seg('init_data', 'token')} · "
                f"领取 {seg('token', 'submit')} · 共 {total:.2f}s")
    # webapp
    arrival = seg('submit', 'amount')
    if arrival == "—" and "submit" in tm:
        arrival = "未响应"
    return (f"响应 {resp} · 验证页 {seg('start_sent', 'webview')} · "
            f"识别提交 {seg('webview', 'submit')} · 到账 {arrival} · "
            f"共 {total:.2f}s")


def _content(mode: str | None, ok: bool, amount: str | None,
             winner: str | None, feedback: str) -> str:
    """领取返回内容（展示 callback/私聊/HTTP 回执原文，缺省给兜底文案）。"""
    feedback = (feedback or "").strip()
    if mode in ("direct", "captcha"):
        return (feedback or f"已领取 {amount or '?'}") if ok else (feedback or "未抢到")
    if mode == "webapp":
        if ok and amount:
            c = f"已领取 {amount}"
            if winner and winner != "direct":
                c += f" ｜ {winner} 路最快"
            return c
        if ok:
            return "已提交验证" + (f"（{winner}）" if winner else "")
        return "未抢到" + (f"（{amount}）" if amount else "")
    # dm_captcha / fulilai
    if ok:
        return feedback or (f"已领取 {amount}" if amount else "已领取")
    return feedback or ("未抢到" + (f"（{amount}）" if amount else ""))


def _sum_amounts(amounts: list[str | None]) -> str:
    """把多笔 '8.2 CNY' 按币种求和 → '15.5 CNY'（多币种用 + 连接）。"""
    sums: dict[str, float] = defaultdict(float)
    order: list[str] = []
    for a in amounts:
        if not a:
            continue
        m = re.match(r"([\d.]+)\s*(.*)", a.strip())
        if not m:
            continue
        try:
            num = float(m.group(1))
        except ValueError:
            continue
        unit = m.group(2).strip()
        if unit not in sums:
            order.append(unit)
        sums[unit] += num
    parts = []
    for unit in order:
        vs = f"{sums[unit]:.2f}".rstrip("0").rstrip(".")
        parts.append(f"{vs} {unit}".strip())
    return " + ".join(parts)


@dataclass
class ClaimReport:
    """单账号领取报告（结构化，render() 出通知文本）。"""
    wallet: str
    wallet_label: str
    kind: str
    kind_label: str
    conditions: list[str]
    conditions_text: str
    packet_amount: str | None
    remaining: str | None
    sender: str | None
    chat_title: str
    ok: bool
    claimed_amount: str | None
    feedback: str | None
    error_reason: str | None
    timings: dict = field(default_factory=dict)
    total_s: float = 0.0

    @classmethod
    def from_context(cls, rp, ok, amount, winner, tm, chat_title="") -> "ClaimReport":
        return cls(
            wallet=getattr(rp, "wallet", "unknown"),
            wallet_label=WALLET_LABELS.get(getattr(rp, "wallet", "unknown"), "未知钱包"),
            kind=rp.kind,
            kind_label=KIND_LABELS.get(rp.kind, rp.kind),
            conditions=list(getattr(rp, "conditions", []) or []),
            conditions_text=conditions_to_text(getattr(rp, "conditions", []) or []),
            packet_amount=getattr(rp, "amount_text", None),
            remaining=getattr(rp, "remaining", None),
            sender=getattr(rp, "sender_name", None),
            chat_title=chat_title,
            ok=ok,
            claimed_amount=amount,
            feedback=tm.get("feedback"),
            error_reason=tm.get("error_reason"),
            timings=tm,
            total_s=round(tm.get("done", 0) - tm.get("detect", 0), 2),
        )

    def render(self) -> str:
        icon = "🧧" if self.ok else "❌"
        head = f"{icon} {'领取成功' if self.ok else '领取失败'} ｜ {self.wallet_label} · {self.kind_label}"
        lines = [head, SEP, f"窗口昵称：{self.chat_title}"]
        lines += _info_lines(self)
        lines.append(SEP)
        content = _content(self.timings.get("mode"), self.ok, self.claimed_amount,
                           self.timings.get("winner"), self.feedback or "")
        lines.append(f"返回：{content}")
        lines.append(f"耗时：{_format_timing(self.timings)}")
        return "\n".join(lines)


def build_report(rp, ok: bool, amount: str | None, winner: str | None,
                 tm: dict, chat_title: str = "") -> str:
    """单账号领取详情（个人通知）。winner 透传给耗时/内容。"""
    tm = dict(tm)
    tm.setdefault("winner", winner)
    return ClaimReport.from_context(rp, ok, amount, winner, tm, chat_title).render()


def build_detect_report(rp, chat_title: str, n_accounts: int) -> str:
    """频道阶段1：检测到红包。"""
    wl = WALLET_LABELS.get(getattr(rp, "wallet", "unknown"), "未知钱包")
    kl = KIND_LABELS.get(rp.kind, rp.kind)
    lines = [f"🧧 检测到红包 ｜ {wl} · {kl}", SEP, f"群组：{chat_title}"]
    lines += _info_lines(rp)
    if getattr(rp, "captcha_expr", None):
        lines.append(f"算式：{rp.captcha_expr}")
    lines.append(f"分发：{n_accounts} 个账号并发领取")
    return "\n".join(lines)


def build_locked_report(rp, chat_title: str) -> str:
    """频道阶段2：未解锁红包（只通知）。"""
    wl = WALLET_LABELS.get(getattr(rp, "wallet", "unknown"), "未知钱包")
    lines = [f"🔒 检测到红包（未解锁）", SEP, f"群组：{chat_title}"]
    amt = _amount_line(rp)
    if amt:
        lines.append(amt)
    lines.append(f"钱包：{wl}")
    if getattr(rp, "conditions", None):
        lines.append(f"条件：{conditions_to_text(rp.conditions)}")
    lines.append("状态：等待解锁")
    return "\n".join(lines)


def build_summary_report(rp, results: list[tuple[str, object]], chat_title: str) -> str:
    """频道阶段3：领取汇总。results=[(账号名, ClaimResult), ...]，逐账号附各自耗时。"""
    wl = WALLET_LABELS.get(getattr(rp, "wallet", "unknown"), "未知钱包")
    kl = KIND_LABELS.get(rp.kind, rp.kind)
    total = len(results)
    ok_results = [(n, r) for n, r in results if getattr(r, "ok", False)]
    head_icon = "🧧" if ok_results else "❌"
    lines = [f"{head_icon} 领取汇总 ｜ {wl} · {kl}", SEP, f"群组：{chat_title}",
             f"成功：{len(ok_results)}/{total} 个账号"]
    totals = _sum_amounts([getattr(r, "amount", None) for _, r in ok_results])
    if totals:
        lines.append(f"总领取：{totals}")
    if getattr(rp, "conditions", None):
        lines.append(f"条件：{conditions_to_text(rp.conditions)}")
    lines.append(SEP)
    for name, r in results:
        t = getattr(r, "total_s", None)
        cost = f" ｜ {t:.2f}s" if t else ""
        if getattr(r, "ok", False):
            amt = getattr(r, "amount", None)
            lines.append(f"✅ {name}：{('+' + amt) if amt else '已领取'}{cost}")
        else:
            reason = ERROR_REASON_LABELS.get(getattr(r, "error_reason", None), "未抢到")
            lines.append(f"❌ {name}：{reason}{cost}")
    return "\n".join(lines)
