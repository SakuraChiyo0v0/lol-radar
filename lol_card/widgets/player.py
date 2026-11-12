"""玩家卡上的组件。box 是排布层分给这个组件的框（x/w 是这一列的范围，y 是顶边）；多数组件在框里水平居中。
每个组件报告自己要多高（measure），排布层据此把下一个组件接在下面。"""
from __future__ import annotations

from dataclasses import dataclass

from ..canvas import fit, name_parts, paste_icon, text_c
from ..layout import Box, Ctx, Node, _check_width
from ..scoring import LABEL, player_score, titles_of
from ..theme import (C_BG0, C_DIM, C_GOLD, C_LINE, C_LOSE, C_PANEL, C_TEXT, C_TRACK, C_WIN, POS_CN,
                     SIDE, STAT_FMT, TEAM_ORDER, TIER_CN, font, num_font, win_color)


DIVISIONS = ("I", "II", "III", "IV")


def rank_text(p: dict) -> str:
    """「#tag · 钻石 I 36LP」；大师 / 宗师 / 王者没有小段，写成「大师 20LP」。
    库里这三个段位的 division 存的是 NA，没段位的 tier 是空串，都不能原样上图。"""
    sub = name_parts(p["name"])[1]
    rk = p.get("rank") or {}
    tier = (rk.get("tier") or "").strip()
    if tier:
        div = (rk.get("division") or "").strip().upper()
        parts = [TIER_CN.get(tier, tier)]
        if div in DIVISIONS:
            parts.append(div)
        parts.append(f"{rk.get('lp', 0)}LP")
        sub += "  ·  " + " ".join(parts)
    return sub.strip(" ·")


# ---------------------------------------------------------------- 身份区

@dataclass
class Avatar(Node):
    """圆形头像（召唤师头像优先，没有就用英雄方图），单局有胜负角标（角标会略微伸出头像下沿）。"""
    size: int = 132

    def measure(self, ctx, p, w) -> int:
        return self.size

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        A, S = ctx.A, self.size
        av = A.profile(p.get("profile_icon")) or (A.champion(p["champion"]) if p.get("champion") else None)
        x, y = box.cx - S // 2, box.y
        paste_icon(ctx.img, av, (x, y, S), circle=True, ring=ctx.color)
        if p.get("win") is not None:
            d = ctx.d
            bx, by = box.cx + S // 2 - 22, y + S - 34
            d.ellipse([bx, by, bx + 40, by + 40], fill=C_WIN if p["win"] else C_LOSE, outline=C_BG0, width=3)
            text_c(d, (bx + 20, by + 5), "胜" if p["win"] else "负", font(20, True), C_TEXT)


@dataclass
class NameLine(Node):
    """名字（去掉 #tag），超宽截断。height = 这一行占的高度。"""
    size: int = 30
    height: int = 42

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        d, f = ctx.d, font(self.size, True)
        text_c(d, (box.cx, box.y), fit(d, name_parts(p["name"])[0], f, box.w - 40), f, C_TEXT)


@dataclass
class RankLine(Node):
    """#tag · 段位。"""
    size: int = 18
    height: int = 28

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        text_c(ctx.d, (box.cx, box.y), rank_text(p), font(self.size), C_DIM)


@dataclass
class KdaLine(Node):
    """单局 K/D/A 或历史场均。"""
    size: int = 40
    height: int = 46

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        text_c(ctx.d, (box.cx, box.y), p.get("kda_line", ""), num_font(self.size), C_TEXT)


@dataclass
class PosLine(Node):
    """位置；样本不足时标红。"""
    size: int = 16
    height: int = 32

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        text_c(ctx.d, (box.cx, box.y), f"{POS_CN.get(p['pos'], p['pos'])}", font(self.size),
               C_LOSE if p.get("low_sample") else C_DIM)


# ---------------------------------------------------------------- 中段（单局装备 / 常用英雄 / 分位置）

@dataclass
class Loadout(Node):
    """一行：[英雄] [召唤师技能 ×2] [基石 + 副系] [装备 3×2] [饰品]，整行在框里居中。
    单局最核心的信息（打的什么、带的什么、出了什么），所以给足尺寸。"""
    champ: int = 84
    icon: int = 40
    sub: int = 28
    gap: int = 4         # 同一块里图标之间
    block_gap: int = 14  # 块与块之间
    trinket: bool = True

    def measure(self, ctx, p, w) -> int:
        return max(self.champ, 2 * self.icon + self.gap)

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        A, img, y = ctx.A, ctx.img, box.y
        I, G, C = self.icon, self.gap, self.champ
        blocks = [("champ", C), ("spells", I), ("runes", I), ("items", 3 * I + 2 * G)]
        if self.trinket:
            blocks.append(("trinket", I))
        total = sum(w for _, w in blocks) + self.block_gap * (len(blocks) - 1)
        _check_width("Loadout", total, box.w)
        x = box.cx - total // 2
        for kind, w in blocks:
            if kind == "champ":
                champ = A.champion(p["champion"]) if p.get("champion") else None
                paste_icon(img, champ, (x, y, C), r=10, ring=C_LINE)
                if p.get("champ_level"):
                    d = ctx.d
                    lv, fl = str(p["champ_level"]), font(17, True)
                    bw = int(d.textlength(lv, font=fl)) + 14
                    d.rounded_rectangle([x + C - bw + 2, y + C - 24, x + C + 2, y + C + 2], 7, fill=C_BG0)
                    text_c(d, (x + C - bw / 2 + 2, y + C - 24), lv, fl, C_TEXT)
            elif kind == "spells":
                for i, sid in enumerate((p.get("spells") or [])[:2]):
                    paste_icon(img, A.spell(sid), (x, y + i * (I + G), I), r=7)
            elif kind == "runes":
                paste_icon(img, A.rune(p.get("keystone")), (x, y, I), circle=True)
                off = (I - self.sub) // 2
                paste_icon(img, A.rune(p.get("sub_style")), (x + off, y + I + G + off // 2, self.sub), circle=True)
            elif kind == "items":
                its = list(p.get("items") or [])[:6]
                for i in range(6):
                    iid = its[i] if i < len(its) else 0
                    paste_icon(img, A.item(iid), (x + (i % 3) * (I + G), y + (i // 3) * (I + G), I), r=7)
            elif kind == "trinket":
                paste_icon(img, A.item(p.get("trinket") or 0), (x, y + (I + G) // 2, I), circle=True, ring=C_LINE)
            x += w + self.block_gap


@dataclass
class TopChamps(Node):
    """常用英雄：最多 limit 个，下面写局数和胜率。"""
    size: int = 52
    gap: int = 22
    limit: int = 4

    def measure(self, ctx, p, w) -> int:
        return self.size + 4 + 18   # 图标 + 间距 + 一行 14px 小字

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        tops = (p.get("top_champs") or [])[:self.limit]
        if not tops:
            return
        d, S = ctx.d, self.size
        x = box.cx - (len(tops) * S + (len(tops) - 1) * self.gap) // 2
        for t in tops:
            paste_icon(ctx.img, ctx.A.champion(t["champion"]), (x, box.y, S), r=8, ring=C_LINE)
            wr = t["wins"] / max(t["games"], 1)
            text_c(d, (x + S // 2, box.y + S + 4), f"{t['games']}局 {wr:.0%}", font(14), C_WIN if wr >= 0.5 else C_DIM)
            x += S + self.gap


@dataclass
class PosBreakdown(Node):
    """整体模式：上野中下辅五格，每格写位置 / 该位置评分 / 局数与占比；没打过的写 --。"""
    cell: int = 80
    gap: int = 8
    height: int = 76

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        got = {r["pos"]: r for r in (p.get("pos_breakdown") or [])}
        if not got:
            return
        d, bw, y = ctx.d, self.cell, box.y
        x = box.cx - (len(TEAM_ORDER) * bw + (len(TEAM_ORDER) - 1) * self.gap) // 2
        tot = sum(r["games"] for r in got.values()) or 1
        for pos in TEAM_ORDER:
            r = got.get(pos)
            d.rounded_rectangle([x, y, x + bw, y + self.height], 10, fill=(30, 40, 66) if r else (22, 30, 50),
                                outline=C_LINE)
            text_c(d, (x + bw // 2, y + 6), POS_CN.get(pos, pos), font(15, True), C_TEXT if r else C_DIM)
            if r:
                sc = r["score"]
                text_c(d, (x + bw // 2, y + 24), f"{sc:.1f}", num_font(30), C_GOLD if sc >= 6 else C_TEXT)
                text_c(d, (x + bw // 2, y + 56), f"{r['games']}局 {r['games'] / tot:.0%}", font(13), C_DIM)
            else:
                text_c(d, (x + bw // 2, y + 24), "--", num_font(30), C_DIM)
                text_c(d, (x + bw // 2, y + 56), "0局", font(13), C_DIM)
            x += bw + self.gap


# ---------------------------------------------------------------- 评分 / 近期 / 明细

SCORE_BARS = (("伤害", "dmg_share"), ("承伤", "tank_share"), ("经济", "gold_per_min"), ("参团", "kp"))


@dataclass
class ScoreBox(Node):
    """评分卡：综合评分大字 + 称号胶囊 + 若干关键指标小条，铺满框宽。
    称号最多 max_titles 个，排成两行两列（一个时和原来一样居中在一行）；只放真正够格的，放不下就少放。"""
    height: int = 188
    bars: tuple = SCORE_BARS
    max_titles: int = 4

    def _pill(self, d, x, y, h, title, good, f):
        tw = d.textlength(title, font=f)
        d.rounded_rectangle([x, y, x + tw + 2 * self._pad(h), y + h], h // 2,
                            fill=(46, 90, 60) if good else (96, 44, 48), outline=C_WIN if good else C_LOSE)
        # 文字基线：h=36 / 20 号字时是 +4，和原来单个称号的位置一模一样
        d.text((x + self._pad(h), y + (h - f.size) // 2 - 4), title, font=f, fill=C_TEXT)
        return tw + 2 * self._pad(h)

    @staticmethod
    def _pad(h):
        return 14 if h >= 36 else 11

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        d, x0, y0, x1, color = ctx.d, box.x, box.y, box.x1, ctx.color
        h = box.h or self.height
        d.rounded_rectangle([x0, y0, x1, y0 + h], 14, fill=C_PANEL + (230,), outline=C_LINE, width=1)
        d.rectangle([x0 + 1, y0 + 14, x0 + 5, y0 + h - 14], fill=color)

        sc = player_score(p)
        titles = titles_of(p["pcts"], p["pos"], self.max_titles)
        d.text((x0 + 28, y0 + 16), "综合评分", font=font(18), fill=C_DIM)
        d.text((x0 + 26, y0 + 34), f"{sc:.1f}", font=num_font(76), fill=C_GOLD if sc >= 6 else C_TEXT)
        tx, right = x0 + 150, x1 - 20
        if len(titles) == 1:
            # 一个称号：和原来完全一样（一行居中，高 36）
            self._pill(d, tx, y0 + 58, 36, *titles[0], font(20, True))
        else:
            # 多个：两行，每行最多两个，高 30、间距 8；一行放不下第二个就挪到下一行，两行都满就不放了
            # 先排版再画：只用到一行时，这一行和单个称号一样竖直居中，不挤在上面
            f, ph, gap = font(17, True), 30, 8
            placed, r, x = [], 0, tx
            for title, good in titles:
                w = d.textlength(title, font=f) + 2 * self._pad(ph)
                if x > tx and x + w > right:
                    r, x = r + 1, tx
                if r >= 2 or x + w > right:
                    break
                placed.append((r, x, title, good))
                x += w + gap
            rows = [y0 + 76 - ph // 2] if r == 0 else [y0 + 40, y0 + 40 + ph + gap]
            for r, x, title, good in placed:
                self._pill(d, x, rows[r], ph, title, good, f)

        n = len(self.bars)
        bw = (box.w - 56 - (n - 1) * 14) // n
        for i, (lbl, k) in enumerate(self.bars):
            bx, by = x0 + 28 + i * (bw + 14), y0 + 132
            d.text((bx, by), lbl, font=font(15), fill=C_DIM)
            v = p["pcts"][k]
            d.rounded_rectangle([bx, by + 24, bx + bw, by + 32], 4, fill=C_TRACK)
            d.rounded_rectangle([bx, by + 24, bx + max(8, int(bw * v / 100)), by + 32], 4, fill=win_color(v, color))
            d.text((bx + bw - d.textlength(f"P{v:.0f}", font=font(14)), by + 2), f"P{v:.0f}",
                   font=font(14), fill=C_TEXT)


@dataclass
class RecentStrip(Node):
    """近 N 局：一行标题 + 一行英雄小图标，绿框赢红框输。"""
    size: int = 36
    gap: int = 7
    limit: int = 10

    def measure(self, ctx, p, w) -> int:
        return 32 + self.size   # 标题行 + 图标

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        rec = (p.get("recent") or [])[:self.limit]
        if not rec:
            return
        wins = sum(1 for r in rec if r["win"])
        ctx.d.text((box.x + 4, box.y), f"近 {len(rec)} 局  {wins} 胜 {len(rec) - wins} 负",
                   font=font(17, True), fill=C_TEXT)
        for i, r in enumerate(rec):
            x = box.x + 4 + i * (self.size + self.gap)
            paste_icon(ctx.img, ctx.A.champion(r["champion"]), (x, box.y + 32, self.size), r=6,
                       ring=C_WIN if r["win"] else C_LOSE)


@dataclass
class DetailPanel(Node):
    """单人模式右栏：八维逐条分位条，从强到弱排。"""
    height: int = 616
    row: int = 62

    def draw(self, ctx: Ctx, p: dict, box: Box) -> None:
        d, x0, y0, x1 = ctx.d, box.x, box.y, box.x1
        d.rounded_rectangle([x0, y0, x1, y0 + self.height], 14, fill=C_PANEL + (230,), outline=C_LINE, width=1)
        d.text((x0 + 28, y0 + 20), "八维明细", font=font(22, True), fill=C_TEXT)
        bw = box.w - 56
        for i, k in enumerate(sorted(p["pcts"], key=lambda k: p["pcts"][k], reverse=True)):
            y = y0 + 96 + i * self.row
            v = p["pcts"][k]
            d.text((x0 + 28, y), LABEL[k], font=font(18), fill=C_TEXT)
            f = font(18)
            pw = d.textlength(f"P{v:.0f}", font=f)
            d.text((x1 - 28 - pw - 90, y), STAT_FMT[k].format(p["stats"][k]), font=f, fill=C_DIM)
            d.text((x1 - 28 - pw, y), f"P{v:.0f}", font=font(18, True),
                   fill=C_GOLD if v >= 75 else (C_TEXT if v >= 40 else C_LOSE))
            by = y + 32
            d.rounded_rectangle([x0 + 28, by, x0 + 28 + bw, by + 10], 5, fill=C_TRACK)
            d.rounded_rectangle([x0 + 28, by, x0 + 28 + max(10, int(bw * v / 100)), by + 10], 5,
                                fill=win_color(v, SIDE[0]))
            mx = x0 + 28 + bw // 2
            d.line([(mx, by - 3), (mx, by + 13)], fill=(200, 208, 226), width=2)
