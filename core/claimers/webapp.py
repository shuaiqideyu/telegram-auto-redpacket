"""模块3 网页验证：/start 取 WebView → AI 多模型并发解图片验证码 → 提交到账。

支持 okpay / kkpay 等钱包的 URL 网页验证码。每个账号各自 RequestWebView 会拿到
一张独立的验证码图片（题目/答案各不相同），故答案无法跨账号共享，必须各解各的——
用多模型并发抢速度。可共享的只有从群消息提取的 start_param。

流程：
1. 给 bot 发 /start <start_param>，事件驱动等回 WebView 按钮
2. 对每个视觉模型各开一条流水线：RequestWebView 取独立验证页 → AI 解验证码 → 提交
3. 任一线路成功即到账；事件驱动等 bot 到账回执
"""
import asyncio
import logging
import time
from pathlib import Path

from telethon import events
from telethon.tl import functions
from telethon.tl.types import KeyboardButtonWebView, Message

from ..browser import launch_browser
from ..config import config
from ..vision.solver import INJECT_LABELS_JS
from .base import EMPTY_KW, ClaimResult, extract_amount

log = logging.getLogger("core.webapp")


async def solve_captcha_page(url, solver, browser=None, model=None, tag="",
                             timings=None, debug=False) -> bool:
    """打开验证页 → 注入标签 → 截图 → AI识别 → 按 data-index 点击 → 提交。
    model 指定识别模型；tag 区分并发线路；timings 回填各阶段时间戳。"""
    own_browser = browser is None
    browser_close = None
    if own_browser:
        browser, browser_close = await launch_browser()

    tg = f"[{tag}] " if tag else ""
    suffix = f"_{tag}" if tag else ""
    page = await browser.new_page(
        viewport={"width": 420, "height": 780},
        device_scale_factor=4,
    )
    try:
        # 1) 加载：等关键元素就绪，而非 networkidle（更快）
        await page.goto(url, wait_until="domcontentloaded", timeout=10000)
        try:
            await page.wait_for_selector(".char-box", timeout=6000)
        except Exception:
            log.warning(f"{tg}验证页未就绪/已过期")
            return False
        # 图片就绪（best-effort，不阻塞太久）
        try:
            await page.wait_for_function(
                "() => Array.from(document.images).every(i => i.complete && i.naturalWidth>0)",
                timeout=2500)
        except Exception:
            pass

        # 2) 注入编号标签（绝对定位，不触发重排）
        await page.evaluate(INJECT_LABELS_JS)

        # 3) 截图验证码区域
        screenshot = await page.locator(".captcha-containers").screenshot(type="png")
        if debug:
            Path("debug").mkdir(exist_ok=True)
            with open(f"debug/labeled{suffix}.png", "wb") as f:
                f.write(screenshot)
        if timings is not None:
            timings["ready"] = time.time()

        # 4) AI 识别
        clicks = await solver.solve(screenshot, model=model)
        if not clicks:
            log.error(f"{tg}识别失败")
            return False

        # 5) 按 data-index 点击（免疫 reflow 重排）
        for idx in clicks:
            await page.locator(f'.char-box[data-index="{idx}"]').click()

        # 6) 提交
        await page.locator("#submitBtn").click()
        if timings is not None:
            timings["submit"] = time.time()

        # 7) 等结果：成功→页面跳转(抛异常)；失败→出现"验证失败"
        try:
            await page.wait_for_function(
                "() => document.body && (document.body.innerText.includes('验证失败')"
                " || document.body.innerText.includes('成功')"
                " || document.body.innerText.includes('領取'))",
                timeout=4000)
            txt = await page.evaluate("() => document.body.innerText")
            if "验证失败" in txt:
                log.debug(f"{tg}验证失败（识别有误）")
                return False
            log.debug(f"{tg}验证通过")
            return True
        except Exception:
            # 页面跳转(成功) 或 超时(已提交，以 okpay 为准)
            log.debug(f"{tg}已提交")
            return True

    except Exception as e:
        log.error(f"{tg}浏览器异常: {e}")
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass
        if own_browser and browser_close:
            await browser_close()


class WebappClaimer:
    """网页验证红包领取器。browser 由调度方预启动并复用。"""

    def __init__(self, client, solver, browser, models=None):
        self.client = client
        self.solver = solver
        self.browser = browser
        self.models = models or config.vision_models

    @staticmethod
    def _classify(t: str):
        if "🎉" in t and any(k in t for k in ("领取了", "領取了", "抢到了", "搶到了", "成功")):
            return ("ok", t)
        if any(k in t for k in EMPTY_KW):
            return ("fail", t)
        if "频繁" in t or "頻繁" in t:
            return ("fail", t)
        return None

    async def claim(self, msg: Message | None, start_param: str, tm: dict,
                    bot_username: str | None = None) -> ClaimResult:
        tm["mode"] = "verify"
        if bot_username:
            bot_entity = await self.client.get_input_entity(bot_username)
        else:
            bot_entity = await self.client.get_input_entity(next(iter(config.okpay_bot_ids)))

        # 获取 bot 的 full entity 用于事件过滤
        bot_full = await self.client.get_entity(bot_entity)
        bot_id = bot_full.id

        loop = asyncio.get_running_loop()
        wv_fut = loop.create_future()
        res_fut = loop.create_future()

        async def _wv_handler(ev):
            mk = ev.message.reply_markup
            if not mk:
                return
            for row in mk.rows:
                for btn in row.buttons:
                    if isinstance(btn, KeyboardButtonWebView) and not wv_fut.done():
                        wv_fut.set_result(btn.url)

        async def _res_handler(ev):
            r = self._classify(ev.message.text or "")
            if r and not res_fut.done():
                res_fut.set_result(r)

        self.client.add_event_handler(_wv_handler, events.NewMessage(from_users=bot_id))
        self.client.add_event_handler(_res_handler, events.NewMessage(from_users=bot_id))
        try:
            await self.client.send_message(bot_entity, f"/start {start_param}")
            tm["start_sent"] = time.time()
            try:
                webview_url = await asyncio.wait_for(wv_fut, 6.0)
            except asyncio.TimeoutError:
                log.debug("未收到验证 WebView，可重试")
                return ClaimResult(False, retryable=True)
            tm["webview"] = time.time()

            tasks = [self._pipeline(bot_entity, webview_url, model, tag)
                     for model, tag in self.models]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            valid = [r for r in results if isinstance(r, dict)]
            submits = [r["submit"] for r in valid if r.get("submit")]
            if submits:
                tm["submit"] = min(submits)
            oks = [r for r in valid if r.get("ok") and r.get("submit")]
            winner = min(oks, key=lambda r: r["submit"])["tag"] if oks else None

            # 等到账结果（延长到 15 秒，bot 响应可能慢）
            try:
                status, text = await asyncio.wait_for(res_fut, 15.0)
            except asyncio.TimeoutError:
                status, text = "timeout", ""
        finally:
            self.client.remove_event_handler(_wv_handler)
            self.client.remove_event_handler(_res_handler)

        amount = extract_amount(text)
        if status == "ok":
            tm["amount"] = time.time()
            log.info(f"💰 {text.strip()}")
            return ClaimResult(True, amount, winner, retryable=False)
        if status == "fail" and text:
            log.info(f"🈳 {text.strip()}")
            return ClaimResult(False, amount, winner, retryable=False)
        # 验证码已提交但未收到到账反馈 → 视为已提交成功（不重试，不再发 /start）
        if oks:
            log.info(f"✅ 验证码已提交（{winner}），等待 bot 确认")
            return ClaimResult(True, None, winner, retryable=False)
        return ClaimResult(False, None, winner, retryable=False)

    async def _pipeline(self, bot, webview_url, model, tag) -> dict:
        """单条并发流水线：独立验证码 + 独立模型 + 独立浏览器页（用后即清理）。"""
        sub_tm: dict = {}
        try:
            res = await self.client(functions.messages.RequestWebViewRequest(
                peer=bot, bot=bot, url=webview_url, platform="android",
            ))
        except Exception as e:
            log.error(f"[{tag}] RequestWebView失败: {e}")
            return {"tag": tag, "ok": False, "submit": None}
        ok = await solve_captcha_page(
            res.url, self.solver, self.browser, model=model, tag=tag, timings=sub_tm)
        return {"tag": tag, "ok": ok, "submit": sub_tm.get("submit")}
