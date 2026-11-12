"""对局清算的组件：五行对位，每行 [蓝方玩家条 | 中间位置 + 差值 | 红方玩家条]。

数据在 ctx.meta["lanes"]：[(蓝方 card 或 None, 红方 card 或 None)] × 最多 5 行，card 字段：
  name, champion, pos(分位用的位置键), lane_pos(峡谷真实分路), team, win, score, pcts, kda_line
ctx.meta["laned"]：峡谷 True（中间写位置），乱斗 False（中间写「第 N」）。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageStat

from ..canvas import fit, name_parts, paste_icon, text_c, text_r
from ..layout import Box, Ctx, Node
from ..scoring import titles_of
from ..theme import C_DIM, C_GOLD, C_LINE, C_LOSE, C_PANEL, C_TEXT, C_WIN, POS_CN, SIDE, font, num_font

EVEN = 0.3          # 两边评分差在这以内算持平
ROW_LUMA = 40       # 玩家条底下原画的平均亮度：各张原画亮暗差很多，统一到这一档
ROW_HI = 80         # 再把高于这个的高光压成 1/3 斜率，局部亮块不盖字


def _score_color(s: float) -> tuple:
    return C_GOLD if s >= 7 else (C_TEXT if s >= 4 else C_LOSE)


def _row_art(A, champ: str, size: tuple[int, int], side: int):
    """玩家条的底图：原画裁成条状，模糊、亮度统一、高光削平、往本侧颜色偏一点。拿不到原画返回 None。
    两边都用原画本来的朝向。"""
    art = A.splash(champ) if champ else None
    if art is None:
        return None
    w, h = size
    k = max(w / art.width, h / art.height)
    s = art.resize((int(art.width * k) + 1, int(art.height * k) + 1), Image.LANCZOS)
    # centered 原画人物在中间偏上：横向居中，纵向取上三分之一处
    x, y = (s.width - w) // 2, max(0, (s.height - h) // 3)
    part = s.crop((x, y, x + w, y + h))
    # 不镜像：原画里人物朝哪边不固定（盖伦朝左、很多人朝右），镜像了反而会朝外，画里的字和武器也会反
    part = part.filter(ImageFilter.GaussianBlur(2))
    luma = ImageStat.Stat(part.convert("L")).mean[0] or 1
    part = ImageEnhance.Brightness(part).enhance(min(1.6, ROW_LUMA / luma))
    part = part.point(lambda v: v if v < ROW_HI else ROW_HI + (v - ROW_HI) // 3)
    return Image.blend(part, Image.new("RGB", size, SIDE[side]), 0.14)


def _fade(size: tuple[int, int], side: int) -> Image.Image:
    """原画往中间淡出：蓝方条从左往右淡，红方条从右往左淡；最淡处也留一点，不至于一刀切。"""
    w, h = size
    row = Image.new("L", (w, 1))
    px = row.load()
    for x in range(w):
        t = x / max(w - 1, 1)
        t = t if side == 0 else 1 - t
        px[x, 0] = int(70 + 185 * (1 - t) ** 0.9)
    return row.resize((w, h))


def _mini_radar(img, cx, cy, r, pcts, keys, color, ghost=None):
    """小雷达：只画形状，不写轴名。ghost = 对位那个人的形状（灰色细线），一眼看出谁哪项高。"""
    n = len(keys)
    ang = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

    def poly(pc):
        out = []
        for k, a in zip(keys, ang):
            v = max(0.06, min(1.0, pc.get(k, 50) / 100))
            out.append((cx + r * v * math.cos(a), cy + r * v * math.sin(a)))
        return out
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay)
    d.polygon([(cx + r * math.cos(a), cy + r * math.sin(a)) for a in ang], fill=(8, 12, 24, 120),
              outline=(90, 110, 150, 170))
    d.polygon([(cx + r * 0.5 * math.cos(a), cy + r * 0.5 * math.sin(a)) for a in ang], outline=(90, 110, 150, 110))
    if ghost:
        d.polygon(poly(ghost), outline=(210, 214, 226, 120))
    d.polygon(poly(pcts), fill=color + (110,), outline=color + (255,))
    img.alpha_composite(lay)


def _pill(d, x, y, text, good, f, h=26) -> int:
    tw = d.textlength(text, font=f)
    d.rounded_rectangle([x, y, x + tw + 20, y + h], h // 2,
                        fill=(46, 90, 60) if good else (96, 44, 48), outline=C_WIN if good else C_LOSE)
    d.text((x + 10, y + (h - f.size) // 2 - 3), text, font=f, fill=C_TEXT)
    return int(tw + 20)


def _arrow(d, x, y, size, left: bool, color) -> None:
    """实心三角箭头（字体里没有 ◀ ▶，画出来的不会变方块）。(x, y) 是箭头竖直中线的中点。"""
    s = size
    if left:
        d.polygon([(x - s, y), (x + s * 0.6, y - s), (x + s * 0.6, y + s)], fill=color)
    else:
        d.polygon([(x + s, y), (x - s * 0.6, y - s), (x - s * 0.6, y + s)], fill=color)


@dataclass
class RecapRows(Node):
    """五行对位。每行高 row_h、行距 gap，从框顶往下排；中间留 mid_w 宽写位置和差值。
    画布 900 高：顶栏 + 比分条 140，间距 4，五行 5×142 + 4×8 = 742，底边留 14。"""
    row_h: int = 142
    gap: int = 8
    mid_w: int = 184
    margin: int = 32

    def measure(self, ctx, p, w) -> int:
        n = len(ctx.meta.get("lanes") or [])
        return n * self.row_h + max(0, n - 1) * self.gap

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        lanes = ctx.meta.get("lanes") or []
        laned = ctx.meta.get("laned", True)
        everyone = [c for pr in lanes for c in pr if c]
        mvp = max(everyone, key=lambda c: c["score"]) if everyone else None
        W = ctx.img.width
        half = (W - 2 * self.margin - self.mid_w) // 2
        for i, (b, r) in enumerate(lanes):
            y = box.y + i * (self.row_h + self.gap)
            for side, c, other in ((0, b, r), (1, r, b)):
                if c is None:
                    continue
                x0 = self.margin if side == 0 else W - self.margin - half
                self._player(ctx, c, other, side, Box(x0, y, half, self.row_h), c is mvp)
            self._center(ctx, b, r, i, laned, W // 2, y)

    # ---- 一个人的条
    def _player(self, ctx: Ctx, c: dict, other: dict | None, side: int, box: Box, is_mvp: bool) -> None:
        img, A = ctx.img, ctx.A
        w, h = box.w, box.h
        # 底：面板色打底，原画往中间淡出叠上去，圆角裁切
        card = Image.new("RGBA", (w, h), C_PANEL + (225,))
        art = _row_art(A, c.get("champion"), (w, h), side)
        if art is not None:
            a = art.convert("RGBA")
            a.putalpha(_fade((w, h), side))
            card.alpha_composite(a)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - 1, h - 1], 14, fill=255)
        img.paste(card, (box.x, box.y), mask)
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([box.x, box.y, box.x1, box.y + h], 14,
                            outline=(C_GOLD if is_mvp else C_LINE), width=3 if is_mvp else 1)

        # 头像 + 名字 + KDA + 称号（贴外侧）；小雷达 + 评分（贴中间）
        S = 76
        if side == 0:
            ax, tx = box.x + 18, box.x + 18 + S + 16
        else:
            ax, tx = box.x1 - 18 - S, box.x1 - 18 - S - 16
        paste_icon(img, A.champion(c["champion"]) if c.get("champion") else None, (ax, box.y + 18, S),
                   r=12, ring=SIDE[side])
        d = ImageDraw.Draw(img)
        fn, fk, ft = font(22, True), num_font(30), font(15, True)
        name = fit(d, name_parts(c["name"])[0], fn, 200)
        if side == 0:
            d.text((tx, box.y + 18), name, font=fn, fill=C_TEXT)
            d.text((tx, box.y + 50), c["kda_line"], font=fk, fill=C_TEXT)
        else:
            text_r(d, (tx, box.y + 18), name, fn, C_TEXT)
            text_r(d, (tx, box.y + 50), c["kda_line"], fk, C_TEXT)
        titles = titles_of(c["pcts"], c["pos"], 2)
        if side == 0:
            x = tx
            for t, g in titles:
                x += _pill(d, x, box.y + 100, t, g, ft) + 6
        else:
            x = tx
            for t, g in reversed(titles):
                x -= int(d.textlength(t, font=ft)) + 20
                _pill(d, x, box.y + 100, t, g, ft)
                x -= 6

        keys = list(c["pcts"])
        rx = box.x1 - 190 if side == 0 else box.x + 190
        _mini_radar(img, rx, box.y + h // 2, 50, c["pcts"], keys, SIDE[side], other["pcts"] if other else None)
        d = ImageDraw.Draw(img)
        s, fs = f"{c['score']:.1f}", num_font(60)
        sw = d.textlength(s, font=fs)
        sx = box.x1 - 24 - sw if side == 0 else box.x + 24
        d.text((sx, box.y + 34), s, font=fs, fill=_score_color(c["score"]))
        if is_mvp:
            fm = font(16, True)
            mw = d.textlength("MVP", font=fm) + 18
            mx = sx + (sw - mw) / 2
            d.rounded_rectangle([mx, box.y + 104, mx + mw, box.y + 128], 12, fill=C_GOLD)
            d.text((mx + 9, box.y + 105), "MVP", font=fm, fill=(10, 14, 28))

    # ---- 中间：位置 + 谁领先多少
    def _center(self, ctx: Ctx, b, r, i: int, laned: bool, cx: int, y: int) -> None:
        d = ctx.d
        pos = (b or r or {}).get("lane_pos") if laned else None
        lab = POS_CN.get(pos, pos or "") if laned else f"第 {i + 1}"
        text_c(d, (cx, y + 28), lab, font(20, True), C_TEXT)
        if not (b and r):
            return
        diff = b["score"] - r["score"]
        if abs(diff) <= EVEN:
            text_c(d, (cx, y + 70), "持平", font(22, True), C_DIM)
            return
        left = diff > 0
        color = SIDE[0] if left else SIDE[1]
        f = num_font(36)
        txt = f"{abs(diff):.1f}"
        tw = d.textlength(txt, font=f)
        ay = y + 90
        if left:
            _arrow(d, cx - tw / 2 - 4, ay, 11, True, color)
            d.text((cx - tw / 2 + 10, y + 70), txt, font=f, fill=color)
        else:
            d.text((cx - tw / 2 - 10, y + 70), txt, font=f, fill=color)
            _arrow(d, cx + tw / 2 + 4, ay, 11, False, color)
        text_c(d, (cx, y + 116), "领先", font(14), C_DIM)
