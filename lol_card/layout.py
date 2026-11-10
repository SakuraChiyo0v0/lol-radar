"""排布层：把组件按坐标放进列，再把列和整页组件拼成一张图。

- 组件（widgets/）只管「在给定的框里画自己」，不知道自己在第几行、在哪一侧。
- 列（Column）= 一组 At(y, 组件)，决定这一列里每个组件放多高。
- 页面（Page）= 若干列 + 整页组件（标题、雷达），决定这张图有哪些块、按什么顺序画。

所以「同样的元素换个排布」= 在 templates.py 里改 At 的顺序或 y，组件代码不用动。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

from PIL import Image, ImageDraw

from .theme import SIDE


@dataclass
class Ctx:
    """一次渲染里所有组件共享的东西。idx/other 在画某一侧时由 Column 填。"""
    img: Image.Image
    A: Any                      # Assets
    meta: dict
    players: list               # 本页全部玩家（雷达等整页组件要用）
    idx: int = 0                # 当前画的是第几侧（0 左蓝，1 右红）
    other: dict | None = None   # 对面那个人的 pcts（没有对手时为 None）

    @property
    def color(self) -> tuple:
        return SIDE[self.idx]

    @property
    def d(self) -> ImageDraw.ImageDraw:
        return ImageDraw.Draw(self.img)


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int = 0

    @property
    def cx(self) -> int:
        return self.x + self.w // 2

    @property
    def x1(self) -> int:
        return self.x + self.w


class Widget(Protocol):
    def draw(self, ctx: Ctx, p: dict | None, box: Box) -> None: ...


@dataclass
class At:
    """把一个组件放在本列的第 y 像素（绝对坐标）；h 给需要固定高度的组件用。"""
    y: int
    widget: Widget
    h: int = 0
    when: Callable[[dict], bool] | None = None   # 条件显示：返回 False 就跳过

    def draw(self, ctx: Ctx, p: dict | None, x: int, w: int) -> None:
        if self.when is None or self.when(p or {}):
            self.widget.draw(ctx, p, Box(x, self.y, w, self.h))


@dataclass
class Column:
    """一列：x/w 定位置，slots 定内容。"""
    x: int
    w: int
    slots: list[At] = field(default_factory=list)

    def draw(self, ctx: Ctx, p: dict | None) -> None:
        for s in self.slots:
            s.draw(ctx, p, self.x, self.w)
