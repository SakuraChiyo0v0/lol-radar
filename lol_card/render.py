"""两个入口：render（对位卡，1–2 人）和 render_team（5v5 全队卡）。
这里只按模板把组件串起来，不写任何坐标。"""
from __future__ import annotations

from pathlib import Path

from . import templates as T
from .assets import Assets
from .canvas import background
from .layout import Box, Ctx
from .theme import TEAM_ORDER, W
from .widgets import LaneCenter
from .widgets.team import LEAD


def _save(img, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=95)


def render(players: list[dict], out: Path, meta: dict, assets: Assets | None = None,
           template: str | None = None) -> None:
    """players: 1–2 个，每个 dict 需要：
      name, pos, games, stats{8 维原始值}, pcts{8 维百分位}
      可选：profile_icon, champion, champ_level, kda_line, items[6], trinket, spells[], keystone, sub_style,
            rank{tier,division,lp}, recent[{win,champion}], top_champs[{champion,games,wins}],
            pos_breakdown[{pos,games,score}], score, win, low_sample, fracs, axes
    meta: title, badge（subtitle/footer 现在不画，留着给调用方记录口径）
    template: TEMPLATES 里的名字，不给就用 default
    """
    ctx = Ctx(background().convert("RGBA"), assets or Assets(), meta, players)
    T.HEADER.draw(ctx, None, Box(0, 0, W))

    slots = T.TEMPLATES.get(template or meta.get("template") or "default", T.TEMPLATES["default"])
    for idx, p in enumerate(players):
        ctx.idx = idx
        ctx.other = players[1 - idx]["pcts"] if len(players) == 2 else None
        T.player_column(idx, slots).draw(ctx, p)

    if len(players) == 1:
        ctx.idx = 0
        T.DETAIL.draw(ctx, players[0], T.CARD_X[1], T.CARD_W)
    T.RADAR.draw(ctx, None, T.RADAR_BOX)
    _save(ctx.img, Path(out))


def render_team(left: list[dict | None], right: list[dict | None], out: Path, meta: dict,
                assets: Assets | None = None) -> None:
    """left/right：按 TEAM_ORDER 排好的 5 个玩家（缺人给 None）。每人需要：
      name, pos, score(None=无数据), pcts；可选 profile_icon, champion, rank, recent, games,
      low_sample, fallback(本位置无记录、按全位置算), missing_player(看不到是谁)
    meta: title, badge, sides=(左名, 右名), note
    """
    meta = {"title": T.TEAM_TITLE, **meta, "_left": left, "_right": right}
    ctx = Ctx(background().convert("RGBA"), assets or Assets(), meta, [])
    T.HEADER.draw(ctx, None, Box(0, 0, W))
    T.TEAM_POWER.draw(ctx, None, Box(0, T.TEAM_POWER_Y, W))

    lane = LaneCenter()
    for i, pos in enumerate(TEAM_ORDER):
        y = T.TEAM_ROWS_TOP + i * (T.TEAM_ROW_H + T.TEAM_ROW_GAP)
        lp = left[i] if i < len(left) else None
        rp = right[i] if i < len(right) else None
        ls = lp.get("score") if lp else None
        rs = rp.get("score") if rp else None
        both = ls is not None and rs is not None
        for side, (pl, lead) in enumerate(((lp, both and ls - rs >= LEAD), (rp, both and rs - ls >= LEAD))):
            ctx.idx = side
            x0, x1 = T.TEAM_SIDE_X[side]
            T.TEAM_ROW.draw(ctx, pl, Box(x0, y, x1 - x0, T.TEAM_ROW_H), lead=lead)
        lane.draw(ctx, pos, Box(0, y, W), ls, rs)
    _save(ctx.img, Path(out))
