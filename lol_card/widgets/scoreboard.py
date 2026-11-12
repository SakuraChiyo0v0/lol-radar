"""单局顶部比分条。数据在 meta.scoreboard（lol_radar.match_teams 的结果）：
  {"teams": {100: {kills, deaths, assists, gold, win, obj{tower,dragon,baron,riftHerald,horde,inhibitor}}, 200: {...}},
   "duration": 秒}
叠在顶栏上画（放在 Layers 里，自己不占排布高度）：
  第一行（顶栏中间）：[胜] 蓝方  27 : 7  红方 [负]
  第二行（顶栏下面、雷达上面的空档）：经济 52.1k : 41.3k   推塔 3 : 0   小龙 2 : 0 …（只列至少一方有数的资源）
"""
from __future__ import annotations

from dataclasses import dataclass

from ..layout import Box, Ctx, Node
from ..theme import C_BG0, C_DIM, C_LOSE, C_TEXT, C_WIN, SIDE, font, num_font

# 第二行的资源，按重要程度排；宽度不够时从后往前丢
OBJECTIVES = (("tower", "推塔"), ("dragon", "小龙"), ("baron", "大龙"), ("horde", "虚空巢虫"),
              ("riftHerald", "先锋"), ("inhibitor", "水晶"))


def _gold(v: int) -> str:
    return f"{v / 1000:.1f}k"


@dataclass
class MatchScoreboard(Node):
    width: int = 640      # 第二行最多这么宽，放不下就少列几项
    row2_dy: int = 104    # 第二行距框顶多少（顶栏高 96，雷达最上面的轴标签在框顶下约 170）
    item_gap: int = 30
    height: int = 0       # 占多少排布高度。单局页叠在顶栏上（0）；清算页单独占一行，下面紧接五行对位

    def measure(self, ctx, p, w) -> int:
        return self.height

    def draw(self, ctx: Ctx, p, box: Box) -> None:
        sb = ctx.meta.get("scoreboard") or {}
        teams = {int(k): v for k, v in (sb.get("teams") or {}).items()}
        b, r = teams.get(100), teams.get(200)
        if not b or not r:
            return
        d, cx, y = ctx.d, box.cx, box.y

        # ---- 第一行：胜负 + 阵营 + 人头比分
        f_num, f_side, f_pill = num_font(60), font(22, True), font(18, True)
        kb, kr = str(b["kills"]), str(r["kills"])

        def pill_w(t):
            return int(d.textlength(t, font=f_pill)) + 24

        def res(t):
            return "胜" if t["win"] else "负"
        parts = [("pill", res(b), b["win"], pill_w(res(b))), ("gap", 12), ("side", "蓝方", SIDE[0]), ("gap", 18),
                 ("num", kb, SIDE[0]), ("gap", 16), ("colon",), ("gap", 16), ("num", kr, SIDE[1]), ("gap", 18),
                 ("side", "红方", SIDE[1]), ("gap", 12), ("pill", res(r), r["win"], pill_w(res(r)))]

        def width(pt):
            k = pt[0]
            if k == "gap":
                return pt[1]
            if k == "pill":
                return pt[3]
            if k == "side":
                return int(d.textlength(pt[1], font=f_side))
            if k == "num":
                return int(d.textlength(pt[1], font=f_num))
            return int(d.textlength(":", font=f_num))
        x = cx - sum(width(pt) for pt in parts) // 2
        for pt in parts:
            k, w = pt[0], width(pt)
            if k == "pill":
                good = pt[2]
                d.rounded_rectangle([x, y + 34, x + w, y + 66], 16, fill=C_WIN if good else C_LOSE, outline=C_BG0)
                d.text((x + 12, y + 37), pt[1], font=f_pill, fill=C_TEXT)
            elif k == "side":
                d.text((x, y + 36), pt[1], font=f_side, fill=pt[2])
            elif k == "num":
                d.text((x, y + 14), pt[1], font=f_num, fill=pt[2])
            elif k == "colon":
                d.text((x, y + 12), ":", font=f_num, fill=C_DIM)
            x += w

        # ---- 第二行：经济 + 有数的资源
        items = [("经济", _gold(b["gold"]), _gold(r["gold"]))]
        ob, orr = b.get("obj") or {}, r.get("obj") or {}
        for key, label in OBJECTIVES:
            vb, vr = int(ob.get(key, 0)), int(orr.get(key, 0))
            if vb or vr:
                items.append((label, str(vb), str(vr)))
        f_lab, f_val, f_sep = font(16), num_font(26), font(16)

        def item_w(it):
            lab, vb, vr = it
            return int(d.textlength(lab, font=f_lab) + 10 + d.textlength(vb, font=f_val)
                       + d.textlength(" : ", font=f_sep) + d.textlength(vr, font=f_val))
        while len(items) > 1 and sum(item_w(it) for it in items) + self.item_gap * (len(items) - 1) > self.width:
            items.pop()
        total = sum(item_w(it) for it in items) + self.item_gap * (len(items) - 1)
        x, y2 = cx - total // 2, y + self.row2_dy
        for lab, vb, vr in items:
            d.text((x, y2 + 6), lab, font=f_lab, fill=C_DIM)
            x += int(d.textlength(lab, font=f_lab)) + 10
            d.text((x, y2), vb, font=f_val, fill=SIDE[0])
            x += int(d.textlength(vb, font=f_val))
            d.text((x, y2 + 6), " : ", font=f_sep, fill=C_DIM)
            x += int(d.textlength(" : ", font=f_sep))
            d.text((x, y2), vr, font=f_val, fill=SIDE[1])
            x += int(d.textlength(vr, font=f_val)) + self.item_gap
