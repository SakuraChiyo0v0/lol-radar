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
