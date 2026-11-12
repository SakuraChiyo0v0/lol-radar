"""底层画图工具：贴图标、居中/右对齐文字、截断、背景、虚线。组件都只用这些。"""
from __future__ import annotations

import math

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .theme import C_BG0, C_BG1, C_SLOT, H, W


def round_mask(size: tuple[int, int], r: int) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, size[0] - 1, size[1] - 1], r, fill=255)
    return m


def paste_icon(img: Image.Image, icon: Image.Image | None, box: tuple[int, int, int], r: int = 6,
               ring: tuple | None = None, circle: bool = False) -> None:
    """把图标贴成 s×s 的圆角方块或圆形；icon 为 None 时画占位块。ring = 描边色。"""
    x, y, s = box
    d = ImageDraw.Draw(img)
    if icon is None:
        if circle:
            d.ellipse([x, y, x + s, y + s], fill=C_SLOT)
        else:
            d.rounded_rectangle([x, y, x + s, y + s], r, fill=C_SLOT)
    else:
        ic = icon.resize((s, s), Image.LANCZOS)
        mask = Image.new("L", (s, s), 0)
        if circle:
            ImageDraw.Draw(mask).ellipse([0, 0, s - 1, s - 1], fill=255)
        else:
            mask = round_mask((s, s), r)
        # 图标自带透明区（ddragon 符文/技能图是透明底），必须和形状掩膜相乘，
        # 否则透明部分会被当成实心贴上去，出现一圈奇怪的底色。
        mask = ImageChops.multiply(mask, ic.getchannel("A"))
        img.paste(ic, (x, y), mask)
    if ring:
        if circle:
            d.ellipse([x - 2, y - 2, x + s + 1, y + s + 1], outline=ring, width=3)
        else:
            d.rounded_rectangle([x - 1, y - 1, x + s, y + s], r + 1, outline=ring, width=2)


def empty_slot(img: Image.Image, box: tuple[int, int, int], r: int = 6) -> None:
    """空装备栏：半透明的暗底 + 一圈淡描边，像游戏里没放东西的格子。
    用半透明而不是实心色块：铺了原画的背景也能透出来，不会是一块突兀的平涂色。"""
    x, y, s = box
    layer = Image.new("RGBA", (s + 2, s + 2), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle([1, 1, s, s], r, fill=(8, 12, 24, 110), outline=(120, 140, 180, 70), width=1)
    img.alpha_composite(layer, (x - 1, y - 1))


def text_c(d, xy, text, font, fill):
    tw = d.textlength(text, font=font)
    d.text((xy[0] - tw / 2, xy[1]), text, font=font, fill=fill)


def text_r(d, xy, text, font, fill):
    tw = d.textlength(text, font=font)
    d.text((xy[0] - tw, xy[1]), text, font=font, fill=fill)


def fit(d, text, font, width):
    """超宽就截断加省略号。"""
    if d.textlength(text, font=font) <= width:
        return text
    while text and d.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…"


def background() -> Image.Image:
    """深蓝渐变 + 中心光晕，和转播板的氛围接近但不抢内容。"""
    bg = Image.new("RGB", (W, H), C_BG0)
    d = ImageDraw.Draw(bg)
    for y in range(H):
        t = y / H
        c = tuple(int(C_BG0[i] + (C_BG1[i] - C_BG0[i]) * (1 - abs(t - 0.45) * 1.6)) for i in range(3))
        d.line([(0, y), (W, y)], fill=c)
    glow = Image.new("L", (W, H), 0)
    ImageDraw.Draw(glow).ellipse([W / 2 - 420, 120, W / 2 + 420, 820], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    return Image.composite(Image.new("RGB", (W, H), (40, 80, 150)), bg, glow)


SPLASH_W = 900          # 每侧原画铺多宽
SPLASH_FADE = (560, 820)  # 左侧原画从 x=560 开始淡出、到 820 完全消失；右侧对称
SPLASH_LUMA = 34        # 压暗后原画的平均亮度（0–255）。各张原画亮暗差很多，统一拉到这个值，字才都看得清
                        # 之后还会把 >70 的高光压成 1/3 斜率，局部亮块也不会盖住文字
SPLASH_TINT = 0.18      # 往本侧颜色（蓝 / 红）偏多少


def _fade_mask(x0: int, x1: int, left: bool) -> Image.Image:
    """水平渐变掩膜：左侧版本在 x0 以左全不透明、x1 以右全透明；右侧版本镜像。"""
    row = Image.new("L", (W, 1))
    px = row.load()
    for x in range(W):
        xx = x if left else W - 1 - x
        t = 0.0 if xx <= x0 else (1.0 if xx >= x1 else (xx - x0) / (x1 - x0))
        px[x, 0] = int(255 * (1 - t) ** 1.4)
    return row.resize((W, H))


def _splash_side(art: Image.Image, side: int, color: tuple) -> Image.Image:
    """一侧的原画层：按高度铺满，取人物所在的中间偏右那段；右侧镜像让人物朝向中间。
    模糊一点去掉细节（不和前景抢），亮度统一压到 SPLASH_LUMA，再往本侧颜色偏一点。"""
    from PIL import ImageEnhance, ImageOps, ImageStat
    s = art.resize((max(SPLASH_W, int(art.width * H / art.height)), H), Image.LANCZOS)
    # centered 原画人物大多在中间：取中间往右的一段，左边的人就站在画布左缘附近
    x = max(0, min(s.width - SPLASH_W, s.width // 2 - SPLASH_W // 3))
    part = s.crop((x, 0, x + SPLASH_W, H))
    if side == 1:
        part = ImageOps.mirror(part)
    part = part.filter(ImageFilter.GaussianBlur(5))
    part = ImageEnhance.Color(part).enhance(0.8)
    luma = ImageStat.Stat(part.convert("L")).mean[0] or 1
    part = ImageEnhance.Brightness(part).enhance(min(1.0, SPLASH_LUMA / luma))
    part = Image.blend(part, Image.new("RGB", part.size, color), SPLASH_TINT)
    # 平均亮度压下去了，局部亮块（火光、高光）还可能盖住文字：把最亮的地方再削平
    part = part.point(lambda v: v if v < 70 else 70 + (v - 70) // 3)
    layer = Image.new("RGB", (W, H), C_BG0)
    layer.paste(part, (0, 0) if side == 0 else (W - SPLASH_W, 0))
    return layer


def splash_background(arts: list, colors: list) -> Image.Image:
    """两侧铺英雄原画的背景：原来的深蓝渐变打底，左右各一张原画往中间淡出，中间留给雷达。
    arts[i] 为 None 的那一侧就不铺（保持原背景）；两侧都没有时和 background() 完全一样。"""
    bg = background()
    if not any(arts):
        return bg
    for side, art in enumerate(arts[:2]):
        if art is None:
            continue
        bg = Image.composite(_splash_side(art, side, colors[side]), bg, _fade_mask(*SPLASH_FADE, side == 0))
    # 四角压暗，视线收到中间
    m = Image.new("L", (W, H), 0)
    ImageDraw.Draw(m).ellipse([-W * 0.15, -H * 0.25, W * 1.15, H * 1.25], fill=255)
    m = m.filter(ImageFilter.GaussianBlur(160))
    from PIL import ImageEnhance
    return Image.composite(bg, ImageEnhance.Brightness(bg).enhance(0.6), m)


def dashed(d, a, b, fill, width, dash=10, gap=7):
    (x1, y1), (x2, y2) = a, b
    L = math.hypot(x2 - x1, y2 - y1)
    if L == 0:
        return
    ux, uy = (x2 - x1) / L, (y2 - y1) / L
    t = 0.0
    while t < L:
        e = min(t + dash, L)
        d.line([(x1 + ux * t, y1 + uy * t), (x1 + ux * e, y1 + uy * e)], fill=fill, width=width)
        t = e + gap


def name_parts(name: str) -> tuple[str, str]:
    """「名字#tag」拆成 (名字, "#tag")；没有 tag 时第二项为空。"""
    if "#" in name:
        n, t = name.split("#", 1)
        return n, f"#{t.split('#')[0]}"
    return name, ""
