"""英雄联盟能力雷达 / 对位卡的渲染包。

  theme.py      尺寸、配色、字体、中文映射
  assets.py     ddragon 图标缓存
  scoring.py    评分、称号（纯计算）
  canvas.py     底层画图工具
  layout.py     Ctx / Box / At / Column：组件怎么摆
  widgets/      组件：Avatar、Loadout、ScoreBox、Radar、TeamRow ……
  templates.py  每种模式的排布（改布局只改这里）
  render.py     入口 render / render_team

  config.py     部署相关设置，全部可用环境变量覆盖（见 .env.example）

数据由仓库根目录的 lol_radar.py 取，这里只画。
"""
from .assets import Assets
from .render import render, render_team
from .scoring import comment, score, team_avg, title_of
from .theme import TEAM_ORDER

__all__ = ["Assets", "render", "render_team", "score", "title_of", "comment", "team_avg", "TEAM_ORDER"]
