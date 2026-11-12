"""两个入口：render（对位卡，1–2 人）和 render_team（5v5 全队卡）。
只负责选模板、建画布、保存；排布全在 templates.py。"""
from __future__ import annotations

from pathlib import Path

from .assets import Assets
from .canvas import background, splash_background
from .config import SPLASH_BG
from .layout import Box, Ctx, draw_page
from .templates import TEMPLATES
from .theme import H, SIDE, W


def _bg(players: list[dict], assets: Assets, meta: dict):
    """对位卡的背景：两侧铺各自英雄的原画（单人时只铺左边）。
    关掉（LOL_RADAR_SPLASH_BG=0 或 meta.splash_bg=False）、没有英雄、原画下载失败，都退回原来的深蓝渐变。"""
    if not SPLASH_BG or meta.get("splash_bg") is False:
        return background()
    arts = [assets.splash(p.get("champion")) if p.get("champion") else None for p in players[:2]]
    return splash_background(arts, SIDE)


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
    assets = assets or Assets()
    ctx = Ctx(_bg(players, assets, meta).convert("RGBA"), assets, meta, players)
    draw_page(ctx, _page(template, meta, "card"), Box(0, 0, W, H))
    _save(ctx.img, out)


def render_recap(lanes: list[tuple[dict | None, dict | None]], out: Path, meta: dict,
                 assets: Assets | None = None, template: str | None = None) -> None:
    """对局清算：lanes = 最多 5 行 (蓝方 card, 红方 card)，card 见 widgets/recap.py。
    meta: title, scoreboard（lol_radar.match_teams 的结果）, laned（峡谷 True / 乱斗 False）, queue_name。
    每行自己铺原画，整页背景用原来的深蓝渐变，不再叠一层原画。"""
    ctx = Ctx(background().convert("RGBA"), assets or Assets(), {**meta, "lanes": lanes}, [])
    draw_page(ctx, _page(template, meta, "recap"), Box(0, 0, W, H))
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
