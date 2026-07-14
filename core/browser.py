"""浏览器后端：统一启动入口，跨平台 + 可切换反检测内核。

- backend=playwright（默认）：用 Playwright 自带 chromium。
  - 本机 macOS：设 CHROME_PATH 指向系统 Chrome；
  - 服务器 Linux：CHROME_PATH 留空 + `playwright install chromium`，headless 跑。
- backend=cloak：用 cloakbrowser（源码级反检测 Chromium，drop-in Playwright）。
  - `pip install cloakbrowser`，二进制首次自动下载；OKPay 若上反爬时启用。

返回 (browser, closer)：browser 与 Playwright 的 Browser 接口一致；closer() 释放底层资源。
"""
import logging

from .config import config

log = logging.getLogger("core.browser")


async def launch_browser(chrome_path: str | None = None):
    backend = (config.browser_backend or "playwright").lower()
    headless = config.headless

    if backend == "cloak":
        # cloakbrowser 自管底层 playwright，launch_async 直接返回 Browser
        from cloakbrowser import launch_async  # type: ignore

        browser = await launch_async(headless=headless)
        log.debug("浏览器内核: cloakbrowser (反检测)")

        async def closer():
            await browser.close()

        return browser, closer

    # 默认 playwright
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    kwargs: dict = {"headless": headless}
    path = chrome_path if chrome_path is not None else config.chrome_path
    if path:  # 留空 → 用 Playwright 自带 chromium（服务器友好）
        kwargs["executable_path"] = path
    browser = await pw.chromium.launch(**kwargs)
    log.debug(f"浏览器内核: playwright ({'系统 Chrome' if path else '自带 chromium'}, "
              f"headless={headless})")

    async def closer():
        await browser.close()
        await pw.stop()

    return browser, closer
