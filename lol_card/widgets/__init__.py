"""可复用的画面组件。每个组件是一个小 dataclass：构造参数是它的样式（尺寸等），
measure() 报告自己要多高，draw(ctx, p, box) 在 box 里画出玩家 p 的这一块。"""
from .header import Header
from .player import (Avatar, DetailPanel, KdaLine, Loadout, NameLine, PosBreakdown, PosLine,
                     RankLine, RecentStrip, ScoreBox, TopChamps)
from .radar import Radar
from .scoreboard import MatchScoreboard
from .team import Lanes, LaneCenter, TeamPowerBar, TeamRow, TeamSide

__all__ = [
    "Header", "Avatar", "NameLine", "RankLine", "KdaLine", "PosLine", "Loadout", "TopChamps",
    "PosBreakdown", "ScoreBox", "RecentStrip", "DetailPanel", "Radar", "MatchScoreboard", "TeamPowerBar", "Lanes",
    "TeamSide", "TeamRow", "LaneCenter",
]
