"""OKPay 验证码识别 - 标签匹配策略。

核心思路：
- 验证码本质是"字形匹配"：在 12 格码池里找出与 3 个目标字相同的字
- 给每个码池格子注入可见编号标签 → AI 无需数格子，直接报编号
- AI 只负责识别匹配，代码按 data-index 点击（免疫 reflow 重排）
"""
import base64
import json
import logging
import re

from openai import AsyncOpenAI

from ..config import config

logger = logging.getLogger("core.vision")

# 在码池格子注入编号标签的 JS（top-left 蓝色小角标）
INJECT_LABELS_JS = """
() => {
  document.querySelectorAll('.char-box').forEach(b => {
    const i = parseInt(b.getAttribute('data-index'), 10) + 1;
    if (b.querySelector('.ai-idx')) return;
    const d = document.createElement('div');
    d.className = 'ai-idx';
    d.textContent = i;
    d.style.cssText = 'position:absolute;top:0;left:0;background:#0040ff;color:#fff;font-size:16px;font-weight:900;padding:1px 4px;border-radius:4px;z-index:99999;line-height:1.3;font-family:Arial,sans-serif;letter-spacing:0.5px;text-shadow:0 0 2px #000;';
    b.style.position = 'relative';
    b.appendChild(d);
  });
}
"""

PROMPT = """验证码图片分两区：
上区：3个清晰目标汉字，红色标注01/02/03表示点击顺序。
下区：12个格子（蓝色角标编号1-12），每格含一个手写汉字+黑色干扰直线。

任务：对每个目标汉字(01/02/03)，在12格中找"同一个字"——只比较汉字笔画结构，完全忽略贯穿的黑色直线。
找到后读取该格左上角的蓝色编号(1-12)。

关键：干扰线是直的粗黑线，不是笔画；汉字笔画有弯折转折。先认出目标字是什么字，再逐格比对。
只返回JSON：{"order":["01字","02字","03字"],"clicks":[编号,编号,编号]}"""


class CaptchaSolver:
    def __init__(self, api_key: str | None = None, base_url: str | None = None,
                 model: str | None = None):
        self.model = model or config.vision_model
        key = api_key if api_key is not None else config.vision_api_key
        base = base_url or config.vision_base_url
        self.client: AsyncOpenAI | None = None
        if key:
            self.client = AsyncOpenAI(api_key=key, base_url=base, timeout=15.0)
            logger.debug(f"视觉API: {self.model}")
        else:
            logger.warning("未配置 VISION_API_KEY")

    async def solve(self, image_data: bytes, model: str | None = None) -> list[int] | None:
        """识别已注入编号标签的验证码截图，返回 3 个要点击的 data-index。
        model 可指定具体模型（用于多模型并发），默认本实例模型。"""
        if not self.client:
            return None

        model = model or self.model
        img_b64 = base64.b64encode(image_data).decode()
        try:
            # extra_body 禁用 thinking（qwen3.6+ 默认开启会输出长篇分析拖慢响应）
            resp = await self.client.chat.completions.create(
                model=model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                        {"type": "text", "text": PROMPT},
                    ],
                }],
                max_tokens=200,
                temperature=0.0,
                extra_body={"enable_thinking": False},
            )
            text = resp.choices[0].message.content.strip()
            return self._parse(text, model)
        except Exception as e:
            logger.error(f"[{model}] 识别失败: {e}")
            return None

    def _parse(self, text: str, model: str = "") -> list[int] | None:
        text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()
        text = re.sub(r"```json\s*", "", text)
        text = re.sub(r"```\s*$", "", text)
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            logger.error(f"无JSON: {text[:120]}")
            return None
        try:
            data = json.loads(m.group())
        except json.JSONDecodeError:
            logger.error(f"JSON解析失败: {text[:120]}")
            return None

        order = data.get("order", [])
        clicks = data.get("clicks", [])

        # AI 返回 1-12（与注入的可见编号一致），转为 data-index 0-11
        if len(clicks) != 3 or any(not isinstance(c, int) or not (1 <= c <= 12) for c in clicks):
            logger.error(f"[{model}] clicks非法: order={order} clicks={clicks}")
            return None
        if len(set(clicks)) != 3:
            logger.warning(f"[{model}] clicks有重复: {clicks}")

        indices = [c - 1 for c in clicks]
        logger.info(f"[{model}] 识别: {order} → 编号 {clicks} → data-index {indices}")
        return indices
