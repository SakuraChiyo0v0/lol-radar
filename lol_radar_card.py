#!/usr/bin/env python3
"""兼容入口：渲染代码已拆到 lol_card/ 包里（组件 + 模板）。

lol_radar.py 和其它脚本仍然 `from lol_radar_card import render, render_team, score, Assets, ...`，
这里原样转发，不用改调用方。新代码请直接 `from lol_card import ...`。
改布局看 lol_card/templates.py，改某个元素长相看 lol_card/widgets/。
"""
from lol_card import Assets, comment, render, render_team, score, team_avg, title_of  # noqa: F401
from lol_card.assets import CHAMP_ALIAS, DD, DD_CACHE  # noqa: F401
from lol_card.scoring import INVERT_TANK, LABEL, TITLES_HI, TITLES_LO, WEIGHTS  # noqa: F401
from lol_card.templates import CARD_W, CARD_X  # noqa: F401
from lol_card.theme import (C_BG0, C_BG1, C_DIM, C_GOLD, C_LINE, C_LOSE, C_PANEL, C_TEXT, C_WIN, FONT,  # noqa: F401
                            FONT_NUM, H, POS_CN, SIDE, TEAM_ORDER, TIER_CN, W)
