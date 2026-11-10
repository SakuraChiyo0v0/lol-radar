from __future__ import annotations

from dataclasses import dataclass

from ..layout import Box, Ctx, Node
from ..theme import C_GOLD, C_LINE, C_LOSE, C_TEXT, font


@dataclass
class Header(Node):
    """顶栏：左上标题 + 右上金色胶囊徽标（meta.title / meta.badge），左右各留 inset 边距。
    meta.small_pool 为真时，徽标左边再加一个红色胶囊「比较池小 · 仅供参考」。"""
    default_title: str = "能力雷达"
    height: int = 96
    inset: int = 48
    small_pool_text: str = "比较池小 · 仅供参考"

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        d, L, R, y = ctx.d, box.x + self.inset, box.x1 - self.inset, box.y
        d.text((L, y + 30), ctx.meta.get("title", self.default_title), font=font(38, True), fill=C_TEXT)
        badge = ctx.meta.get("badge")
        if badge:
            f = font(20, True)
            tw = d.textlength(badge, font=f)
            d.rounded_rectangle([R - tw - 28, y + 36, R, y + 74], 19, fill=(32, 46, 80), outline=C_LINE)
            d.text((R - tw - 14, y + 42), badge, font=f, fill=C_GOLD)
            R = R - tw - 28 - 12
        if ctx.meta.get("small_pool"):
            f = font(18, True)
            t = self.small_pool_text
            tw = d.textlength(t, font=f)
            d.rounded_rectangle([R - tw - 28, y + 37, R, y + 73], 18, fill=(96, 44, 48), outline=C_LOSE)
            d.text((R - tw - 14, y + 44), t, font=f, fill=C_TEXT)
