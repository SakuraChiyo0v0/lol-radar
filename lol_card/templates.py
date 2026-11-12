"""每种页面的排布。改布局只改这里：换顺序、改间距、换尺寸、加条件显示、换整页结构。

不写坐标：组件自己报告多高，VStack 把下一个接在上一个下面，HStack 从左往右分宽度。
想把装备区放大，改 Loadout(icon=44)，下面的评分卡、近 10 局会自动往下让；
想调两个元素之间的距离，改它们中间的 Gap。画完如果超出画布，stderr 会提示。

容器的用法见 layout.py 开头的说明。
"""
from __future__ import annotations

from .layout import Choose, Gap, HStack, Layers, OneOf, Side, VStack
from .theme import W
from .widgets import (Avatar, DetailPanel, Header, KdaLine, LaneCenter, Lanes, Loadout, MatchScoreboard, NameLine,
                      PosBreakdown, PosLine, Radar, RankLine, RecapRows, RecentStrip, ScoreBox, TeamPowerBar, TeamRow,
                      TeamSide, TopChamps)

MARGIN = 32      # 画布左右留白
CARD_W = 440     # 玩家卡列宽；中间剩下的宽度给雷达
CARD_X = [MARGIN, W - MARGIN - CARD_W]   # 兼容旧代码


# ---- 条件
def has_items(p):        # 单局：有装备
    return p.get("items") is not None


def has_breakdown(p):    # 整体模式：有分位置评分
    return bool(p.get("pos_breakdown"))


def two_players(ctx):
    return len(ctx.players) == 2


# ---- 玩家卡：一列从上往下
# 中段按数据选：单局 → 装备区，整体模式 → 分位置评分，其余 → 常用英雄。
# OneOf 按三者里最高的占位，所以不管哪个模式，评分卡都在同一高度。
PLAYER_CARD = VStack([
    Avatar(),
    Gap(10),
    NameLine(),
    RankLine(),
    KdaLine(),
    PosLine(),
    OneOf([
        (has_items, Loadout()),
        (has_breakdown, PosBreakdown()),
        (None, TopChamps()),
    ]),
    Gap(30),
    ScoreBox(),
    Gap(44),
    RecentStrip(),
])

# ---- 对位卡页面：顶栏 + [左玩家卡 | 雷达 | 右玩家卡]，只有一个人时右列换成八维明细
CARD_PAGE = VStack([
    Header(),
    Gap(28),
    HStack([
        (CARD_W, Side(0, PLAYER_CARD)),
        (None, Radar(score_badge=False)),    # 两人评分已经在左右评分卡里，中心不再写，免得挡住小的多边形
        (CARD_W, Choose(two_players, Side(1, PLAYER_CARD), Side(0, DetailPanel()))),
    ], pad=MARGIN),
])

# ---- 单局页面：和对位卡一样，只是顶栏上叠一条比分（胜负 + 人头 + 经济 + 资源）
# 比分条叠在顶栏中间和下面的空档里，不占排布高度，所以下面的玩家卡、雷达位置都不动
MATCH_PAGE = VStack([
    Layers([Header(), MatchScoreboard()]),
    Gap(28),
    HStack([
        (CARD_W, Side(0, PLAYER_CARD)),
        (None, Radar(score_badge=False)),
        (CARD_W, Choose(two_players, Side(1, PLAYER_CARD), Side(0, DetailPanel()))),
    ], pad=MARGIN),
])

# ---- 对局清算：顶栏 + 比分条（两行，占 96）+ 五行对位
# 顶栏高 96，比分条第一行叠在顶栏中间；第二行（经济、资源）放在顶栏下面，Layers 把它们画在一起，
# 所以这一块总高 = 比分条第二行底部（row2_dy 104 + 字高约 34）≈ 140
RECAP_PAGE = VStack([
    Layers([Header(), MatchScoreboard(height=140)]),
    Gap(4),
    RecapRows(),
])

# ---- 5v5 全队页面：顶栏 + 战力条 + 五路，每路 [左玩家条 | 位置对比 | 右玩家条]
TEAM_ROW_W = 680
TEAM_PAGE = VStack([
    Header(default_title="战前对位"),
    Gap(26),
    TeamPowerBar(),
    Lanes(HStack([
        (TEAM_ROW_W, TeamSide(0, TeamRow())),
        (None, LaneCenter()),
        (TEAM_ROW_W, TeamSide(1, TeamRow())),
    ], pad=MARGIN), gap=10),
])

# 页面名 → 排布。render() 默认用 card，render_team() 默认用 team；
# 想给某个模式单独排，在这里加一份（比如 "match": MATCH_PAGE），调用时传 template="match"
# 或在 meta 里写 "template": "match"。
TEMPLATES = {
    "card": CARD_PAGE,
    "match": MATCH_PAGE,
    "recap": RECAP_PAGE,
    "team": TEAM_PAGE,
}
