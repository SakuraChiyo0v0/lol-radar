"""排布层：组件报告自己要多高，容器按顺序自动往下（往右）摆。模板里只写顺序和间距，不写坐标。

组件和容器都是「节点」，都有两个方法：
  measure(ctx, p, w) -> int   宽度为 w 时需要多高
  draw(ctx, p, box)           在 box 里画出来
p 是这个节点要画的数据（某个玩家）；整页节点（顶栏、雷达）的 p 是 None，它们从 ctx 拿数据。

容器：
  VStack([...], gap=, pad=)                从上往下排，每个子节点自动接在上一个下面
  HStack([(宽, 节点), ...], gap=, pad=)     从左往右排；宽写 None 表示分走剩下的宽度
  Gap(n)                                   留 n 像素空白
  Side(i, 节点)                            把第 i 个玩家交给节点画，侧别（颜色、朝向）设成 i
  OneOf([(条件, 节点), ...])               按玩家数据选第一个满足条件的节点；条件写 None 表示兜底。
                                           高度取所有候选里最高的，所以不同模式下面的元素位置一致
  Choose(条件, a, b)                       按整页情况（比如有几个人）二选一

画完会检查内容有没有超出画布，超出就往 stderr 打一行提示（不报错，图照样出）。
"""
from __future__ import annotations

import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable

from PIL import Image, ImageDraw

from .theme import SIDE


@dataclass
class Ctx:
    """一次渲染里所有节点共享的东西。idx/other/lead 由 Side、Lane 在画某一侧时填。"""
    img: Image.Image
    A: Any                      # Assets
    meta: dict
    players: list               # 本页全部玩家（雷达等整页组件要用）
    idx: int = 0                # 当前画的是第几侧（0 左蓝，1 右红）
    other: dict | None = None   # 对面那个人的 pcts（没有对手时为 None）
    teams: tuple | None = None  # 5v5：(左队 5 人, 右队 5 人)
    lane: int = 0               # 5v5：当前画第几路
    lead: bool = False          # 5v5：这一路是不是本侧领先
    bottom: int = 0             # 已画内容的最低点，用来检查是否超出画布

    @property
    def color(self) -> tuple:
        return SIDE[self.idx]

    @property
    def d(self) -> ImageDraw.ImageDraw:
        return ImageDraw.Draw(self.img)

    @contextmanager
    def side(self, idx: int, lead: bool = False):
        saved = (self.idx, self.other, self.lead)
        self.idx, self.lead = idx, lead
        self.other = self.players[1 - idx]["pcts"] if len(self.players) == 2 else None
        try:
            yield
        finally:
            self.idx, self.other, self.lead = saved


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

    @property
    def y1(self) -> int:
        return self.y + self.h


class Node:
    """节点基类。组件有 height 字段的，measure 默认就返回它；尺寸随样式变的组件自己重写 measure。"""
    z = 0   # 同一个 HStack 里 z 大的后画（压在上面）

    def measure(self, ctx: Ctx, p, w: int) -> int:
        return int(getattr(self, "height", 0) or 0)

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        raise NotImplementedError


def _warn(msg: str) -> None:
    print(f"[lol_card] {msg}", file=sys.stderr)


def _check_width(name: str, need: int, w: int) -> None:
    """组件内容比分到的宽度还宽时提示（会压到旁边的元素上）。"""
    if need > w:
        _warn(f"{name} 需要宽 {need}，只分到 {w}，会压到旁边的元素")


# ---------------------------------------------------------------- 容器

@dataclass
class Gap(Node):
    height: int

    def draw(self, ctx, p, box) -> None:
        pass


@dataclass
class VStack(Node):
    children: list
    gap: int = 0
    pad: int = 0     # 左右各缩进多少
    z: int = 0

    def measure(self, ctx, p, w) -> int:
        hs = [h for h in (c.measure(ctx, p, w - 2 * self.pad) for c in self.children) if h > 0]
        return sum(hs) + self.gap * max(0, len(hs) - 1)

    def draw(self, ctx, p, box) -> None:
        x, w, y = box.x + self.pad, box.w - 2 * self.pad, box.y
        first = True
        for c in self.children:
            h = c.measure(ctx, p, w)
            if h <= 0:
                continue   # 隐藏的节点（没有这个玩家等）不占位，也不加间距
            if not first:
                y += self.gap
            c.draw(ctx, p, Box(x, y, w, h))
            y += h
            first = False
            ctx.bottom = max(ctx.bottom, y)


@dataclass
class HStack(Node):
    children: list   # [(宽 或 None, 节点)]
    gap: int = 0
    pad: int = 0
    z: int = 0

    def _widths(self, w: int) -> list[int]:
        inner = w - 2 * self.pad - self.gap * (len(self.children) - 1)
        fixed = sum(cw for cw, _ in self.children if cw is not None)
        flex = sum(1 for cw, _ in self.children if cw is None)
        rest = inner - fixed
        if rest < 0:
            _warn(f"横排放不下：定宽合计 {fixed}，可用 {inner}")
        each = max(0, rest) // flex if flex else 0
        return [cw if cw is not None else each for cw, _ in self.children]

    def measure(self, ctx, p, w) -> int:
        return max((n.measure(ctx, p, cw) for cw, (_, n) in zip(self._widths(w), self.children)), default=0)

    def draw(self, ctx, p, box) -> None:
        x, placed = box.x + self.pad, []
        for cw, (_, n) in zip(self._widths(box.w), self.children):
            placed.append((n, Box(x, box.y, cw, box.h)))
            x += cw + self.gap
        for n, b in sorted(placed, key=lambda t: getattr(t[0], "z", 0)):   # 稳定排序：同 z 按从左到右
            n.draw(ctx, p, b)


@dataclass
class Side(Node):
    """把第 idx 个玩家交给 child；没有这个玩家时整块不占位。"""
    idx: int
    child: Node

    def _p(self, ctx):
        return ctx.players[self.idx] if self.idx < len(ctx.players) else None

    def measure(self, ctx, p, w) -> int:
        q = self._p(ctx)
        if q is None:
            return 0
        with ctx.side(self.idx):
            return self.child.measure(ctx, q, w)

    def draw(self, ctx, p, box) -> None:
        q = self._p(ctx)
        if q is not None:
            with ctx.side(self.idx):
                self.child.draw(ctx, q, box)


@dataclass
class OneOf(Node):
    """按 p 选第一个条件成立的候选画。高度取全部候选的最大值：不管选中哪个，下面的元素都在同一高度。"""
    options: list    # [(条件(p)->bool 或 None, 节点)]

    def _pick(self, p):
        for pred, n in self.options:
            if pred is None or pred(p or {}):
                return n
        return None

    def measure(self, ctx, p, w) -> int:
        return max((n.measure(ctx, p, w) for _, n in self.options), default=0)

    def draw(self, ctx, p, box) -> None:
        n = self._pick(p)
        if n is not None:
            n.draw(ctx, p, Box(box.x, box.y, box.w, n.measure(ctx, p, box.w)))


@dataclass
class Choose(Node):
    """按整页情况二选一，pred 收 ctx。"""
    pred: Callable[[Ctx], bool]
    yes: Node
    no: Node
    z: int = 0

    def _pick(self, ctx) -> Node:
        return self.yes if self.pred(ctx) else self.no

    def measure(self, ctx, p, w) -> int:
        return self._pick(ctx).measure(ctx, p, w)

    def draw(self, ctx, p, box) -> None:
        self._pick(ctx).draw(ctx, p, box)


def draw_page(ctx: Ctx, page: Node, box: Box) -> None:
    page.draw(ctx, None, box)
    if ctx.bottom > box.y1:
        _warn(f"布局超出画布：内容画到 y={ctx.bottom}，画布高 {box.y1}，底部会被裁掉")


__all__ = ["Ctx", "Box", "Node", "Gap", "VStack", "HStack", "Side", "OneOf", "Choose", "draw_page"]
