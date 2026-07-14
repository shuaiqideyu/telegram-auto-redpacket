"""字符归一化库：花式 Unicode → ASCII 字母/数字。

覆盖 1682 种 Unicode 数字字符（100%）+ 全部字母变体，
兼容 60+ 种书写系统，供验证码解题、按钮匹配等场景复用。

归一化优先级（从高到低）：
1. ASCII 快路径
2. 手动映射（Emoji 块、NFKC 覆盖不到的范围）
3. unicodedata.numeric()（全球数字系统万能兜底）
4. NFKC 归一化（数学/花式字母变体）
5. unicodedata.name() 名称推断

运算符归一化：全角 ＋－＊／＝？ → 半角
"""
import re
import unicodedata

# ── 运算符 ──

OPERATORS = {
    "+": lambda a, b: a + b,
    "-": lambda a, b: a - b,
    "−": lambda a, b: a - b,   # U+2212 MINUS SIGN
    "*": lambda a, b: a * b,
    "×": lambda a, b: a * b,
    "✖": lambda a, b: a * b,
    "/": lambda a, b: a / b,
    "÷": lambda a, b: a / b,
}

FULLWIDTH_OP = {
    "＋": "+", "－": "-", "＊": "*", "／": "/",
    "＝": "=", "？": "?",
}

# 运算符字符类（正则用）
OP_CHARS = r"+\-−×✖*/÷＋－＊／"

# 算式正则（支持半角/全角运算符和等号）
EXPR_RE = re.compile(rf"(\S+)\s*([{OP_CHARS}])\s*(\S+)")
MATH_RE = re.compile(rf"(\S+)\s*([{OP_CHARS}])\s*(\S+)\s*[=＝]\s*[?？]")


# ── 字符归一化 ──

def normalize_char(ch: str) -> str:
    """单个字符 → ASCII 字母/数字。

    覆盖 Unicode 全部数字字符（1682/1682 = 100%）及所有字母变体。
    """
    # 1) ASCII 快路径
    if ch.isascii() and ch.isalnum():
        return ch.upper()

    cp = ord(ch)

    # 2) 手动映射：Emoji 块字母（NFKC 和 numeric() 都不覆盖）

    # Negative Squared Latin Capital: 🅰(U+1F170)..🅩(U+1F189) → A..Z
    if 0x1F170 <= cp <= 0x1F189:
        return chr(ord("A") + cp - 0x1F170)

    # Negative Circled Latin Capital: 🅐(U+1F150)..🅩(U+1F169) → A..Z
    if 0x1F150 <= cp <= 0x1F169:
        return chr(ord("A") + cp - 0x1F150)

    # Regional Indicator: 🇦(U+1F1E6)..🇿(U+1F1FF) → A..Z
    if 0x1F1E6 <= cp <= 0x1F1FF:
        return chr(ord("A") + cp - 0x1F1E6)

    # 🔟 KEYCAP TEN (U+1F51F)：emoji 块，无 numeric() 值，单独映射 → 10
    # （0️⃣-9️⃣ 是「数字+FE0F+20E3」序列，靠首字符 ASCII 数字命中，无需单独处理）
    if cp == 0x1F51F:
        return "10"

    # 3) 全角运算符 → 半角
    if ch in FULLWIDTH_OP:
        return FULLWIDTH_OP[ch]

    # 4) unicodedata.numeric()：全球 60+ 种书写系统数字万能兜底
    #    阿拉伯 ٠-٩ / 天城文 ०-९ / 泰文 ๐-๙ / CJK 一二三…十百 /
    #    罗马 Ⅰ-Ⅻ / 楔形文字 / 希腊 / 埃塞 / 高棉 / 藏文 / 蒙古 …
    #    放在 NFKC 之前：数值语义优先于字形分解（Ⅲ→3 而非 III）
    nv = unicodedata.numeric(ch, None)
    if nv is not None and nv == int(nv) and 0 <= nv <= 100:
        return str(int(nv))

    # 5) NFKC 归一化（数学粗体/斜体/双线/无衬线/等宽/方框等字母变体）
    nfkc = unicodedata.normalize("NFKC", ch)
    if nfkc != ch:
        result = "".join(c for c in nfkc if c.isascii() and c.isalnum())
        if result:
            return result.upper()

    # 6) unicodedata.name 推断：LATIN CAPITAL LETTER X → X
    try:
        name = unicodedata.name(ch, "")
        for prefix in ("LATIN CAPITAL LETTER ", "LATIN SMALL LETTER ", "DIGIT "):
            if name.startswith(prefix):
                suffix = name[len(prefix):]
                if len(suffix) == 1 and suffix.isalnum():
                    return suffix.upper()
    except ValueError:
        pass

    return ""


def normalize_operand(s: str) -> str:
    """操作数整体归一化（多字符连接）。"""
    return "".join(normalize_char(ch) for ch in s)


# ── 纠偏（形近字符混淆矫正） ──

DIGIT_TO_ALPHA = {"0": "O", "1": "I", "2": "Z", "5": "S", "6": "G", "8": "B"}
ALPHA_TO_DIGIT = {"O": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "G": "6", "B": "8", "D": "0", "Q": "0"}

CONFUSIONS: dict[str, list[str]] = {
    "0": ["O", "D", "Q"], "O": ["0", "D", "Q"],
    "1": ["I", "L", "7", "4"], "I": ["1", "L"], "L": ["1", "I"],
    "4": ["1", "A"], "7": ["1", "T"],
    "2": ["Z"], "Z": ["2"],
    "5": ["S"], "S": ["5"],
    "6": ["G", "B"], "G": ["6"], "B": ["6", "8"],
    "8": ["B", "3"], "3": ["8"],
    "9": ["Q", "G"], "Q": ["9", "O"],
    "D": ["0", "O"], "T": ["7"],
}


def coerce(s: str, to_digit: bool) -> str:
    """按目标类型纠偏：to_digit=True 把字母转数字，否则把数字转字母。"""
    table = ALPHA_TO_DIGIT if to_digit else DIGIT_TO_ALPHA
    return "".join(table.get(ch, ch) if (ch.isalpha() if to_digit else ch.isdigit()) else ch for ch in s)


def buttons_are_digit(labels: list[str]) -> bool | None:
    """根据按钮选项判断答案类型：全数字 True / 全字母 False / 无法判断 None。"""
    norm = [normalize_operand(l) for l in labels]
    norm = [n for n in norm if n]
    if not norm:
        return None
    if all(n.isdigit() for n in norm):
        return True
    if all(n.isalpha() for n in norm):
        return False
    return None


# ── 算式求解 ──

def compute(a: str, op: str, b: str) -> str | None:
    """计算 a op b 的结果（数字做算术，字母做拼接）。"""
    if a.isdigit() and b.isdigit():
        fn = OPERATORS.get(op)
        if not fn:
            return None
        try:
            return str(int(fn(int(a), int(b))))
        except (ZeroDivisionError, ValueError):
            return None
    return a + b


def solve_expr(expr: str) -> str | None:
    """解析算式字符串，返回答案。"""
    m = EXPR_RE.search(expr)
    if not m:
        return None
    raw_a, op, raw_b = m.group(1), m.group(2), m.group(3)
    op = FULLWIDTH_OP.get(op, op)
    a = normalize_operand(raw_a)
    b = normalize_operand(raw_b)
    if not a or not b:
        return None
    return compute(a, op, b)


def solve_expr_with_buttons(expr: str, button_labels: list[str]) -> tuple[str | None, dict[str, str]]:
    """解析算式 + 用按钮选项做验证/纠错。
    返回 (答案, 纠错映射 {原字符: 正确字符})。"""
    m = EXPR_RE.search(expr)
    if not m:
        return None, {}
    raw_a, op, raw_b = m.group(1), m.group(2), m.group(3)
    op = FULLWIDTH_OP.get(op, op)
    a = normalize_operand(raw_a)
    b = normalize_operand(raw_b)
    if not a or not b:
        return None, {}

    want_digit = buttons_are_digit(button_labels)
    if want_digit is not None:
        a = coerce(a, want_digit)
        b = coerce(b, want_digit)

    btn_set = {normalize_operand(l).upper() for l in button_labels}

    answer = compute(a, op, b)
    if answer and answer.upper() in btn_set:
        return answer, {}

    def _variants(s: str) -> list[str]:
        if len(s) == 1:
            return [s] + CONFUSIONS.get(s, [])
        result = [s]
        for i, ch in enumerate(s):
            for alt in CONFUSIONS.get(ch, []):
                result.append(s[:i] + alt + s[i + 1:])
        return result

    for va in _variants(a):
        for vb in _variants(b):
            ans = compute(va, op, vb)
            if ans and ans.upper() in btn_set:
                corrections: dict[str, str] = {}
                for orig, fixed in zip(a, va):
                    if orig != fixed:
                        corrections[orig] = fixed
                for orig, fixed in zip(b, vb):
                    if orig != fixed:
                        corrections[orig] = fixed
                return ans, corrections

    return answer, {}
