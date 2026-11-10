"""画布尺寸、配色、字体、文案映射。改风格只动这里。"""
from __future__ import annotations

from functools import lru_cache

from PIL import ImageFont

from .config import FONT, FONT_BOLD_INDEX, FONT_NUM

W, H = 1600, 900

C_BG0 = (10, 14, 28)
C_BG1 = (18, 30, 58)
C_PANEL = (20, 28, 48)
C_LINE = (52, 66, 98)
C_TEXT = (236, 240, 248)
C_DIM = (146, 158, 184)
C_GOLD = (240, 200, 110)
C_WIN = (70, 190, 120)
C_LOSE = (220, 80, 80)
C_SLOT = (34, 44, 70)        # 图标缺图时的占位底色
C_TRACK = (40, 50, 76)       # 进度条底槽
SIDE = [(70, 170, 255), (255, 120, 90)]   # 左蓝右红，与雷达系列色一致

TIER_CN = {"IRON": "黑铁", "BRONZE": "青铜", "SILVER": "白银", "GOLD": "黄金", "PLATINUM": "铂金",
           "EMERALD": "翡翠", "DIAMOND": "钻石", "MASTER": "大师", "GRANDMASTER": "宗师", "CHALLENGER": "王者"}
POS_CN = {"TOP": "上单", "JUNGLE": "打野", "MIDDLE": "中单", "BOTTOM": "下路", "UTILITY": "辅助",
          "ALL": "全位置", "ARAM": "大乱斗"}
TEAM_ORDER = ["TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"]

# 雷达默认八轴：(轴名, 指标 key, 原始值格式)
DEFAULT_AXES = [
    ("伤害占比", "dmg_share", "{:.1%}"), ("伤害转化", "dmg_per_gold", "{:.0f}"),
    ("经济/分", "gold_per_min", "{:.0f}"), ("补刀/分", "cs_per_min", "{:.1f}"),
    ("参团率", "kp", "{:.0%}"), ("视野/分", "vision_per_min", "{:.2f}"),
    ("KDA", "kda", "{:.2f}"), ("承伤占比", "tank_share", "{:.1%}"),
]
# 明细面板里各指标原始值的格式
STAT_FMT = {"dmg_share": "{:.1%}", "dmg_per_gold": "{:.0f}", "gold_per_min": "{:.0f}", "cs_per_min": "{:.1f}",
            "kp": "{:.0%}", "vision_per_min": "{:.2f}", "kda": "{:.2f}", "tank_share": "{:.1%}",
            "dmg_per_min": "{:.0f}"}


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False):
    return ImageFont.truetype(str(FONT), size, index=FONT_BOLD_INDEX if bold else 0)


@lru_cache(maxsize=32)
def num_font(size: int):
    try:
        return ImageFont.truetype(str(FONT_NUM), size)
    except Exception:
        return font(size, True)


def win_color(v: float, mid=None) -> tuple:
    """分位条配色：≥60 绿，40–60 用本侧颜色（或给定中间色），<40 红。"""
    return C_WIN if v >= 60 else ((mid or SIDE[0]) if v >= 40 else C_LOSE)
