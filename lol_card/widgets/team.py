"""5v5 全队卡的组件：顶部战力条、按路展开的容器、每路的玩家条和中间的位置对比。
数据都在 ctx.teams = (左队 5 人, 右队 5 人) 里，按 TEAM_ORDER 排好，缺人为 None。"""
from __future__ import annotations

from dataclasses import dataclass

from ..canvas import fit, name_parts, paste_icon, text_c, text_r
from ..layout import Box, Ctx, Node
from ..scoring import team_avg, title_of
from ..theme import (C_BG0, C_DIM, C_GOLD, C_LINE, C_LOSE, C_PANEL, C_TEXT, C_TRACK, C_WIN, POS_CN, SIDE,
                     TEAM_ORDER, font, num_font)
from .player import rank_text

LEAD = 0.3   # 评分差 ≥ 这个值才算领先


def _lane_scores(ctx: Ctx):
    left, right = ctx.teams
    i = ctx.lane
    lp = left[i] if i < len(left) else None
    rp = right[i] if i < len(right) else None
    return (lp.get("score") if lp else None), (rp.get("score") if rp else None)


@dataclass
class TeamPowerBar(Node):
    """全队战力：两侧队名 + 平均分大字，中间一根按比例分色的条和结论，下面一行说明（meta.note）。
    左右各留 inset，色条两端各缩进 bar_inset。"""
    height: int = 110
    inset: int = 48
    bar_inset: int = 250

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        d, meta, y0 = ctx.d, ctx.meta, box.y
        L, R = box.x + self.inset, box.x1 - self.inset
        names = meta.get("sides") or ("蓝色方", "红色方")
        la, ra = team_avg(ctx.teams[0]), team_avg(ctx.teams[1])
        d.text((L, y0), names[0], font=font(22, True), fill=SIDE[0])
        text_r(d, (R, y0), names[1], font(22, True), SIDE[1])
        d.text((L, y0 + 26), f"{la:.1f}" if la is not None else "--", font=num_font(60), fill=SIDE[0])
        text_r(d, (R, y0 + 26), f"{ra:.1f}" if ra is not None else "--", num_font(60), SIDE[1])
        bx0, bx1, by = box.x + self.bar_inset, box.x1 - self.bar_inset, y0 + 54
        lv, rv = (la if la is not None else 5.0), (ra if ra is not None else 5.0)
        split = int(bx0 + (bx1 - bx0) * lv / max(lv + rv, 0.1))
        d.rounded_rectangle([bx0, by, bx1, by + 16], 8, fill=SIDE[1])
        d.rounded_rectangle([bx0, by, split, by + 16], 8, fill=SIDE[0])
        d.rectangle([split - 2, by - 4, split + 2, by + 20], fill=C_TEXT)
        if la is not None and ra is not None:
            diff = la - ra
            verdict = "势均力敌" if abs(diff) < LEAD else f"{names[0] if diff > 0 else names[1]} 战力领先 {abs(diff):.1f}"
        else:
            verdict = "数据不足"
        text_c(d, (box.cx, y0 + 12), verdict, font(22, True), C_GOLD)
        if meta.get("note"):
            text_c(d, (box.cx, y0 + 80), meta["note"], font(15), C_DIM)


@dataclass
class Lanes(Node):
    """按 TEAM_ORDER 每路画一次 child（一行），行间距 gap；画第几路由 ctx.lane 告诉子节点。"""
    child: Node
    gap: int = 10

    def measure(self, ctx, p, w) -> int:
        h = self.child.measure(ctx, p, w)
        return len(TEAM_ORDER) * h + (len(TEAM_ORDER) - 1) * self.gap

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        h, saved = self.child.measure(ctx, p, box.w), ctx.lane
        for i in range(len(TEAM_ORDER)):
            ctx.lane = i
            self.child.draw(ctx, p, Box(box.x, box.y + i * (h + self.gap), box.w, h))
            ctx.bottom = max(ctx.bottom, box.y + i * (h + self.gap) + h)
        ctx.lane = saved


@dataclass
class TeamSide(Node):
    """把本路第 idx 队的人交给 child，并设好侧别和「本路是否领先」。"""
    idx: int
    child: Node

    def measure(self, ctx, p, w) -> int:
        return self.child.measure(ctx, None, w)

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        team = ctx.teams[self.idx]
        q = team[ctx.lane] if ctx.lane < len(team) else None
        ls, rs = _lane_scores(ctx)
        both = ls is not None and rs is not None
        lead = both and ((ls - rs) if self.idx == 0 else (rs - ls)) >= LEAD
        saved = (ctx.idx, ctx.lead)
        ctx.idx, ctx.lead = self.idx, lead
        try:
            self.child.draw(ctx, q, box)
        finally:
            ctx.idx, ctx.lead = saved


@dataclass
class TeamRow(Node):
    """一路一个人：头像（右下角叠英雄）、名字、段位、近况，另一端是评分大字和称号。
    ctx.idx 决定朝向：0 左侧（头像在左），1 右侧（镜像）；ctx.lead 为真时描边加粗高亮。"""
    avatar: int = 80
    height: int = 112

    def draw(self, ctx: Ctx, p: dict | None, box: Box) -> None:
        img, A, side, color, lead = ctx.img, ctx.A, ctx.idx, ctx.color, ctx.lead
        d = ctx.d
        x0, x1, y, h = box.x, box.x1, box.y, box.h or self.height
        d.rounded_rectangle([x0, y, x1, y + h], 14, fill=C_PANEL + (230,),
                            outline=color if lead else C_LINE, width=2 if lead else 1)
        if side == 0:
            d.rectangle([x0 + 1, y + 12, x0 + 5, y + h - 12], fill=color)
        else:
            d.rectangle([x1 - 5, y + 12, x1 - 1, y + h - 12], fill=color)

        S = self.avatar
        ax = x0 + 18 if side == 0 else x1 - 18 - S
        ay = y + (h - S) // 2
        tx = ax + S + 18 if side == 0 else ax - 18

        def put(yy, text, f, fill):
            if side == 0:
                d.text((tx, yy), text, font=f, fill=fill)
            else:
                text_r(d, (tx, yy), text, f, fill)

        if not p or p.get("missing_player"):
            paste_icon(img, None, (ax, ay, S), circle=True, ring=C_LINE)
            put(y + 26, (p or {}).get("name") or "未知玩家", font(22, True), C_DIM)
            put(y + 60, (p or {}).get("why") or "看不到是谁", font(16), C_DIM)
            return

        av = A.profile(p.get("profile_icon")) or (A.champion(p["champion"]) if p.get("champion") else None)
        paste_icon(img, av, (ax, ay, S), circle=True, ring=color)
        if p.get("champion") and p.get("profile_icon"):
            cs = 36
            cxx = ax + S - cs + 8 if side == 0 else ax - 8
            paste_icon(img, A.champion(p["champion"]), (cxx, ay + S - cs + 8, cs), r=8, ring=C_BG0)

        put(y + 12, fit(d, name_parts(p["name"])[0], font(24, True), 360), font(24, True), C_TEXT)
        put(y + 46, rank_text(p) or " ", font(16), C_DIM)

        rec = p.get("recent") or []
        w = sum(1 for r in rec if r["win"])
        form = f"近{len(rec)}局 {w}胜{len(rec) - w}负" if rec else "近期无对局"
        if p.get("score") is None:
            note, warn = "窗口内没有对局", True
        elif p.get("fallback"):
            note, warn = "本位置无记录 · 按全位置算", True
        else:
            note, warn = f"本位置 {p.get('games', 0)} 局", bool(p.get("low_sample"))
        put(y + 74, f"{form} · {note}", font(15), C_LOSE if warn else C_DIM)

        sc = p.get("score")
        txt = f"{sc:.1f}" if sc is not None else "--"
        fnum, fill = num_font(58), C_GOLD if (sc or 0) >= 6 else C_TEXT
        if side == 0:
            text_r(d, (x1 - 22, y + 8), txt, fnum, fill)
        else:
            d.text((x0 + 22, y + 8), txt, font=fnum, fill=fill)
        if sc is not None:
            title, good = title_of(p["pcts"], p["pos"])
            f = font(16, True)
            tw = d.textlength(title, font=f)
            if side == 0:
                px1 = x1 - 22
                px0 = px1 - tw - 24
            else:
                px0 = x0 + 22
                px1 = px0 + tw + 24
            d.rounded_rectangle([px0, y + 72, px1, y + 100], 14,
                                fill=(46, 90, 60) if good else (96, 44, 48), outline=C_WIN if good else C_LOSE)
            d.text((px0 + 12, y + 75), title, font=f, fill=C_TEXT)


@dataclass
class LaneCenter(Node):
    """每路中间：位置名 + 左右评分对比条 + 差值，在框里水平居中。"""
    half: int = 64
    height: int = 112
    z: int = 1   # 和两侧玩家条并排时最后画

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        d, cx, y, half = ctx.d, box.cx, box.y, self.half
        ls, rs = _lane_scores(ctx)
        pos = TEAM_ORDER[ctx.lane]
        text_c(d, (cx, y + 14), POS_CN.get(pos, pos), font(20, True), C_TEXT)
        d.rounded_rectangle([cx - half, y + 52, cx + half, y + 60], 4, fill=C_TRACK)
        if ls is not None:
            d.rounded_rectangle([cx - max(6, int(half * ls / 10)), y + 52, cx, y + 60], 4, fill=SIDE[0])
        if rs is not None:
            d.rounded_rectangle([cx, y + 52, cx + max(6, int(half * rs / 10)), y + 60], 4, fill=SIDE[1])
        d.line([(cx, y + 47), (cx, y + 65)], fill=C_TEXT, width=2)
        if ls is not None and rs is not None:
            diff = ls - rs
            if abs(diff) < LEAD:
                text_c(d, (cx, y + 74), "持平", font(18, True), C_DIM)
            else:
                text_c(d, (cx, y + 74), f"+{abs(diff):.1f}", font(18, True), SIDE[0] if diff > 0 else SIDE[1])
        else:
            text_c(d, (cx, y + 74), "--", font(18, True), C_DIM)
