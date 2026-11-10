from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image, ImageDraw

from ..canvas import dashed, fit, name_parts, text_c
from ..layout import Box, Ctx, Node
from ..scoring import player_score
from ..theme import C_BG0, C_DIM, C_GOLD, C_TEXT, DEFAULT_AXES, SIDE, font, num_font


@dataclass
class Radar(Node):
    """八维雷达（整页组件，画 ctx.players 里的全部玩家）。圆心在框的水平中点、距框顶 center_dy 处。
    第二个人画虚线外框（色盲友好）；两人时圆心写评分对比；底下是图例。"""
    radius: int = 238
    center_dy: int = 362
    score_badge: bool = True
    legend: bool = True
    z: int = 1   # 和玩家卡并排时最后画

    def measure(self, ctx, p, w) -> int:
        return self.center_dy + self.radius + (92 + 22 if self.legend else 54 + 24)

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        img, players, R = ctx.img, ctx.players, self.radius
        axes = players[0].get("axes") or DEFAULT_AXES
        cx, cy, n = box.cx, box.y + self.center_dy, len(axes)
        ang = [-math.pi / 2 + 2 * math.pi * i / n for i in range(n)]

        def pt(i, r):
            return (cx + r * math.cos(ang[i]), cy + r * math.sin(ang[i]))

        # 底盘：最外圈实底 + 5 档多边形，隔档填色
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        ld = ImageDraw.Draw(layer)
        ld.polygon([pt(i, R + 14) for i in range(n)], fill=(14, 22, 42, 235), outline=(70, 90, 130, 255))
        for k in range(5, 0, -1):
            r = R * k / 5
            ld.polygon([pt(i, r) for i in range(n)], fill=(28, 42, 74, 140) if k % 2 else (20, 32, 58, 140),
                       outline=(64, 82, 120, 200))
        for i in range(n):
            ld.line([pt(i, 0), pt(i, R)], fill=(64, 82, 120, 160))
        img.alpha_composite(layer)

        # 系列
        for idx, pl in enumerate(players):
            color = SIDE[idx]
            fr = pl.get("fracs") or {}
            pts = [pt(i, R * max(0.04, min(1.30, fr.get(k, max(4, min(100, pl["pcts"][k])) / 100))))
                   for i, (_, k, _) in enumerate(axes)]
            lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ImageDraw.Draw(lay).polygon(pts, fill=color + (70,))
            img.alpha_composite(lay)
            d = ImageDraw.Draw(img)
            for a, b in zip(pts, pts[1:] + pts[:1]):
                if idx == 0:
                    d.line([a, b], fill=color + (255,), width=3)
                else:
                    dashed(d, a, b, color + (255,), 3)
            for (x, y) in pts:
                d.ellipse([x - 5, y - 5, x + 5, y + 5], fill=color, outline=C_BG0, width=2)

        # 轴标签：名称 + 各人原始值（按侧上色），强项（P≥75）名称加金色
        d = ImageDraw.Draw(img)
        fl, fv = font(20, True), font(16)
        for i, (lbl, k, fmt) in enumerate(axes):
            x, y = pt(i, R + 54)
            best = max(pl["pcts"][k] for pl in players)
            text_c(d, (x, y - 26), lbl, fl, C_GOLD if best >= 75 else C_TEXT)
            vals = [fmt.format(pl["stats"][k]) for pl in players]
            if len(vals) == 2:
                t0, t1 = vals
                w0, w1 = d.textlength(t0, font=fv), d.textlength(t1, font=fv)
                sx = x - (w0 + w1 + 14) / 2
                d.text((sx, y + 2), t0, font=fv, fill=SIDE[0])
                d.text((sx + w0 + 14, y + 2), t1, font=fv, fill=SIDE[1])
            else:
                text_c(d, (x, y + 2), vals[0], fv, SIDE[0])

        if self.score_badge and len(players) == 2:
            s0, s1 = player_score(players[0]), player_score(players[1])
            f = num_font(30)
            txt = f"{s0:.1f} : {s1:.1f}"
            tw = d.textlength(txt, font=f)
            d.rounded_rectangle([cx - tw / 2 - 16, cy - 22, cx + tw / 2 + 16, cy + 22], 12, fill=(10, 14, 28, 220))
            d.text((cx - tw / 2, cy - 17), f"{s0:.1f}", font=f, fill=SIDE[0])
            d.text((cx - tw / 2 + d.textlength(f"{s0:.1f} ", font=f), cy - 17), ":", font=f, fill=C_DIM)
            d.text((cx + tw / 2 - d.textlength(f"{s1:.1f}", font=f), cy - 17), f"{s1:.1f}", font=f, fill=SIDE[1])

        if self.legend:
            yy, lx = cy + R + 92, cx - 150
            for idx, pl in enumerate(players):
                c, x = SIDE[idx], lx + idx * 170
                if idx == 0:
                    d.line([(x, yy + 11), (x + 30, yy + 11)], fill=c, width=3)
                else:
                    dashed(d, (x, yy + 11), (x + 30, yy + 11), c, 3)
                d.text((x + 38, yy), fit(d, name_parts(pl["name"])[0], font(16), 110), font=font(16), fill=C_DIM)
