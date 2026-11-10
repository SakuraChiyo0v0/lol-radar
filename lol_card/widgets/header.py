from __future__ import annotations

from dataclasses import dataclass

from ..layout import Box, Ctx
from ..theme import C_GOLD, C_LINE, C_TEXT, W, font


@dataclass
class Header:
    """顶栏：左上标题 + 右上金色胶囊徽标（meta.title / meta.badge）。"""
    default_title: str = "能力雷达"

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        d = ctx.d
        d.text((48, 30), ctx.meta.get("title", self.default_title), font=font(38, True), fill=C_TEXT)
        badge = ctx.meta.get("badge")
        if badge:
            f = font(20, True)
            tw = d.textlength(badge, font=f)
            d.rounded_rectangle([W - 48 - tw - 28, 36, W - 48, 74], 19, fill=(32, 46, 80), outline=C_LINE)
            d.text((W - 48 - tw - 14, 42), badge, font=f, fill=C_GOLD)
