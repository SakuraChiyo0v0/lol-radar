"""每种模式的排布。改布局只改这里：换组件顺序、改 y、换尺寸、加条件显示。

一张对位卡 = 顶栏 + 左右两列玩家卡 + 中间雷达（单人时右列换成八维明细）。
玩家卡这一列从上到下的元素都一样（头像、名字、段位、战绩、位置、中段、评分卡、近 10 局），
不同模式只有「中段」不一样：
  - 单局（match）：Loadout 装备/符文/技能
  - 整体（overall）：PosBreakdown 分位置评分
  - 其余（duel/champ/aram/单人）：TopChamps 常用英雄
所以这里用 when= 按数据选中段，一份 PLAYER_COLUMN 覆盖所有模式。
要给某个模式单独排（比如单局把装备放到评分卡下面），复制一份列、改 y，再在 TEMPLATES 里指过去。
"""
from __future__ import annotations

from .layout import At, Box, Column
from .theme import W
from .widgets import (Avatar, DetailPanel, Header, KdaLine, Loadout, NameLine, PosBreakdown, PosLine, Radar,
                      RankLine, RecentStrip, ScoreBox, TeamPowerBar, TeamRow, TopChamps)

CARD_W = 440
CARD_X = [32, W - 32 - CARD_W]   # 左右两列；中间 720 给雷达


def _has_items(p):
    return p.get("items") is not None


def _has_breakdown(p):
    return p.get("items") is None and bool(p.get("pos_breakdown"))


def _champs(p):
    return p.get("items") is None and not p.get("pos_breakdown")


# 玩家卡：一列从上到下。y 是绝对像素。
PLAYER_SLOTS = [
    At(124, Avatar()),
    At(266, NameLine()),
    At(308, RankLine()),
    At(336, KdaLine()),
    At(382, PosLine()),
    At(414, Loadout(), when=_has_items),
    At(414, PosBreakdown(), when=_has_breakdown),
    At(414, TopChamps(), when=_champs),
    At(528, ScoreBox()),
    At(760, RecentStrip()),
]


def player_column(idx: int, slots=None) -> Column:
    return Column(CARD_X[idx], CARD_W, list(slots or PLAYER_SLOTS))


# 整页组件
HEADER = Header()
RADAR = Radar()
RADAR_BOX = Box(W // 2, 486, 0)          # 雷达圆心 (cx=800, cy=486)
DETAIL = At(124, DetailPanel())           # 单人模式右列


# 模式 → 列排布。现在所有对位卡模式共用 PLAYER_SLOTS（中段按数据自动选）；
# 想让某个模式长得不一样，在这里给它换一份 slots 即可。
TEMPLATES: dict[str, list] = {
    "default": PLAYER_SLOTS,
}


# ---- 5v5 全队卡
TEAM_TITLE = "战前对位"
TEAM_POWER_Y = 122
TEAM_ROWS_TOP, TEAM_ROW_H, TEAM_ROW_GAP = 232, 112, 10
TEAM_SIDE_X = [(32, 712), (888, W - 32)]   # 左右两侧玩家条的 x 范围
TEAM_POWER = TeamPowerBar()
TEAM_ROW = TeamRow()
