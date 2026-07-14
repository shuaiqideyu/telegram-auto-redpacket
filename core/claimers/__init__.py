"""领取模块子包：五种红包领取实现。

- direct     : 模块1 关键词领取（群内 callback 一步到账，最快）
- captcha    : 模块2 窗口验证码（群内算式，答案跨账号共享）
- webapp     : 模块3 网页验证（URL 图片验证码，AI 多模型并发，okpay/kkpay）
- dm_captcha : 模块4 私信验证码（群内触发 → bot 私聊解算式，wlqb 等）
- fulilai    : 模块5 福利来红包（hCaptcha token 池 + HTTP，一 token 一号）
"""
from .base import ClaimResult
from .captcha import CaptchaClaimer
from .direct import DirectClaimer
from .dm_captcha import DmCaptchaClaimer
from .fulilai import FulilaiClaimer
from .webapp import WebappClaimer, solve_captcha_page

__all__ = [
    "ClaimResult", "CaptchaClaimer", "DirectClaimer", "DmCaptchaClaimer",
    "FulilaiClaimer", "WebappClaimer", "solve_captcha_page",
]
