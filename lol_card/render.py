"""两个入口：render（对位卡，1–2 人）和 render_team（5v5 全队卡）。
只负责选模板、建画布、保存；排布全在 templates.py。"""
from __future__ import annotations

from pathlib import Path

from .assets import Assets
from .canvas import background
from .layout import Box, Ctx, draw_page
from .templates import TEMPLATES
from .theme import H, W


def _page(template: str | None, meta: dict, default: str):
    name = template or meta.get("template") or default
    if name not in TEMPLATES:
        raise ValueError(f"没有叫 {name!r} 的模板，可用：{', '.join(TEMPLATES)}")
    return TEMPLATES[name]


def _save(img, out: Path) -> None:
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.convert("RGB").save(out, quality=95)


def render(players: list[dict], out: Path, meta: dict, assets: Assets | None = None,
           template: str | None = None) -> None:
    """players: 1–2 个，每个 dict 需要：
      name, pos, games, stats{8 维原始值}, pcts{8 维百分位}
      可选：profile_icon, champion, champ_level, kda_line, items[6], trinket, spells[], keystone, sub_style,
            rank{tier,division,lp}, recent[{win,champion}], top_champs[{champion,games,wins}],
            pos_breakdown[{pos,games,score}], score, win, low_sample, fracs, axes
    meta: title, badge, template（subtitle/footer 现在不画，留着给调用方记录口径）
    template: TEMPLATES 里的页面名，默认 card
    """
    ctx = Ctx(background().convert("RGBA"), assets or Assets(), meta, players)
    draw_page(ctx, _page(template, meta, "card"), Box(0, 0, W, H))
    _save(ctx.img, out)


def render_team(left: list[dict | None], right: list[dict | None], out: Path, meta: dict,
                assets: Assets | None = None, template: str | None = None) -> None:
    """left/right：按 TEAM_ORDER 排好的 5 个玩家（缺人给 None）。每人需要：
      name, pos, score(None=无数据), pcts；可选 profile_icon, champion, rank, recent, games,
      low_sample, fallback(本位置无记录、按全位置算), missing_player(看不到是谁)
    meta: title, badge, sides=(左名, 右名), note, template
    """
    ctx = Ctx(background().convert("RGBA"), assets or Assets(), meta, [], teams=(left, right))
    draw_page(ctx, _page(template, meta, "team"), Box(0, 0, W, H))
    _save(ctx.img, out)
