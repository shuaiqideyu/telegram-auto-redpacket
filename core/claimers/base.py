"""抢包结果与结果文本解析（各 claimer 共用）。"""
import re
from dataclasses import dataclass


@dataclass
class ClaimResult:
    """一次领取的结果。retryable=True 表示反馈不明（可能识别有误），值得再抢一轮。"""
    ok: bool
    amount: str | None = None
    winner: str | None = None      # 胜出线路 tag（webapp 多模型并发用）/ 'direct'
    retryable: bool = True
    error_reason: str | None = None  # 失败归因：'exhausted'|'blocked'|'wrong_captcha'|'premium'（供汇总展示）
    total_s: float | None = None   # 本账号从分发到出结果的耗时（runner 填，汇总逐账号展示）


# 到账成功关键词（注意：EMPTY_KW 在各 claimer 里先于 SUCCESS_KW 判断，
# 故「已领取过红包」含「领取红包」也不会误判为成功）
SUCCESS_KW = ("领取了", "領取了", "成功", "领取红包", "領取紅包", "抢到", "搶到")
# 已被领完/过期关键词 → 不再重试
EMPTY_KW = ("已被领完", "已被領完", "手慢", "已领完", "已過期", "已过期",
            "被抢完", "已抢完", "已领取过", "已領取過")
# 门槛/权限不满足（发言次数、被禁言等）→ 秒级重试也无意义，不再重试
BLOCKED_KW = ("次数不足", "發言次數不足", "发言不足", "条件不满足", "條件不滿足",
              "不满足", "不滿足", "无权限", "無權限", "被禁言", "被限制", "已禁言")
# 验证码答错 → emoji 识别错误，需要重新识别并更新映射
WRONG_KW = ("验证码错误", "驗證碼錯誤", "答案错误", "答案錯誤", "回答错误",
            "回答錯誤", "答错", "選擇錯誤", "选择错误")

# 锚定 🎉/🧧 后的第一个「数字+单位」（兼容 +60.72 这类带正号、WLCOIN 等任意币种）
_AMOUNT_RE = re.compile(r"[🎉🧧].*?([\d]+\.?\d*)\s*([A-Za-z\u4e00-\u9fff]+)")


def extract_amount(text: str) -> str | None:
    """从 🎉/🧧 开头的反馈文本中提取金额+单位。
    锚定后的第一个 数字+单位 组合，兼容所有币种。"""
    m = _AMOUNT_RE.search(text or "")
    return f"{m.group(1)} {m.group(2)}" if m else None


def derive_error_reason(feedback: str | None) -> str | None:
    """从领取反馈文本归因失败类型，供频道汇总展示。返回 None 表示无法归因。"""
    t = feedback or ""
    if not t:
        return None
    if any(k in t for k in EMPTY_KW):
        return "exhausted"
    if any(k in t for k in WRONG_KW):
        return "wrong_captcha"
    if any(k in t for k in BLOCKED_KW):
        return "blocked"
    if "Premium" in t or "会员" in t:
        return "premium"
    return None
