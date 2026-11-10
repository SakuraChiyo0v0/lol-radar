"""评分、称号、点评——纯计算，不画图。lol_radar.py 也直接用 score()。"""
from __future__ import annotations

TITLES_HI = {
    "dmg_share": "输出核心", "dmg_per_gold": "伤转怪", "gold_per_min": "经济机器",
    "cs_per_min": "补刀大师", "kp": "团战发动机", "vision_per_min": "视野之神",
    "kda": "不死之身", "tank_share": "人肉盾牌",
}
TITLES_LO = {
    "dmg_share": "刮痧师傅", "dmg_per_gold": "经济黑洞", "gold_per_min": "穷鬼",
    "cs_per_min": "补刀困难户", "kp": "单机玩家", "vision_per_min": "视野盲人",
    "kda": "送分童子", "tank_share": "隐身人",
}
LABEL = {
    "dmg_share": "伤害占比", "dmg_per_gold": "伤害转化", "gold_per_min": "经济",
    "cs_per_min": "补刀", "kp": "参团", "vision_per_min": "视野", "kda": "KDA", "tank_share": "承伤",
    "dmg_per_min": "输出",
}
# 位置权重：承伤对坦克/打野是正向，对 C 位只给小权重；辅助视野权重高、补刀几乎不算
WEIGHTS = {
    "TOP":     {"dmg_share": 1.0, "dmg_per_gold": 1.0, "gold_per_min": 0.8, "cs_per_min": 1.0, "kp": 0.8, "vision_per_min": 0.5, "kda": 1.2, "tank_share": 0.8},
    "JUNGLE":  {"dmg_share": 0.8, "dmg_per_gold": 0.8, "gold_per_min": 0.8, "cs_per_min": 0.6, "kp": 1.4, "vision_per_min": 1.0, "kda": 1.2, "tank_share": 0.6},
    "MIDDLE":  {"dmg_share": 1.3, "dmg_per_gold": 1.2, "gold_per_min": 0.9, "cs_per_min": 1.0, "kp": 1.0, "vision_per_min": 0.5, "kda": 1.2, "tank_share": 0.2},
    "BOTTOM":  {"dmg_share": 1.4, "dmg_per_gold": 1.2, "gold_per_min": 1.0, "cs_per_min": 1.2, "kp": 0.8, "vision_per_min": 0.4, "kda": 1.2, "tank_share": 0.1},
    "UTILITY": {"dmg_share": 0.5, "dmg_per_gold": 0.8, "gold_per_min": 0.3, "cs_per_min": 0.1, "kp": 1.5, "vision_per_min": 1.6, "kda": 1.2, "tank_share": 0.5},
}
# 只有 C 位的承伤高要反着算（站位差），在评分里取 100-P
INVERT_TANK = {"MIDDLE", "BOTTOM"}
# 整体模式：评分已在各位置算好再加权，这里只给称号/点评用；承伤跨位置含义相反，不参与
WEIGHTS["ALL"] = {k: 1.0 for k in WEIGHTS["MIDDLE"]}
WEIGHTS["ARAM"] = {k: 1.0 for k in WEIGHTS["MIDDLE"]}
WEIGHTS["ARAM"].update({"dmg_per_min": 1.0, "vision_per_min": 0.0})
WEIGHTS["ALL"]["tank_share"] = 0.3


def score(pcts: dict, pos: str) -> float:
    """八维百分位按位置加权，映射到 0–10（一位小数）。P50 全中位≈5.0。"""
    w = WEIGHTS.get(pos, WEIGHTS["MIDDLE"])
    tot = sum(w.values())
    s = 0.0
    for k, v in pcts.items():
        if k == "tank_share" and pos in INVERT_TANK:
            v = 100 - v
        s += w.get(k, 1) * v
    return round(s / tot / 10, 1)


def player_score(p: dict) -> float:
    """调用方算好了评分（整体模式按位置加权）就用它，否则按八维分位现算。"""
    return p["score"] if p.get("score") is not None else score(p["pcts"], p["pos"])


def title_of(pcts: dict, pos: str) -> tuple[str, bool]:
    """称号取最突出的一维：最高 ≥80 给褒义，否则最低 ≤20 给贬义，都没有就按评分给个中性称号。"""
    w = WEIGHTS.get(pos, WEIGHTS["MIDDLE"])
    ranked = [(k, v) for k, v in pcts.items() if w.get(k, 1) >= 0.5]
    if pos in INVERT_TANK:
        ranked = [(k, v) for k, v in ranked if k != "tank_share"]
    hi = max(ranked, key=lambda kv: kv[1])
    lo = min(ranked, key=lambda kv: kv[1])
    if hi[1] >= 80:
        return TITLES_HI[hi[0]], True
    if lo[1] <= 20:
        return TITLES_LO[lo[0]], False
    sc = score(pcts, pos)
    return ("稳定发挥" if sc >= 5 else "中规中矩"), sc >= 5


def comment(pcts: dict, pos: str, other: dict | None) -> str:
    """一句点评：说最强项和最弱项；有对手时补一句领先/落后几维。卡片上已不画，留给调用方用。"""
    w = WEIGHTS.get(pos, WEIGHTS["MIDDLE"])
    keys = [k for k in pcts if w.get(k, 1) >= 0.5 and not (k == "tank_share" and pos in INVERT_TANK)]
    hi = max(keys, key=lambda k: pcts[k])
    lo = min(keys, key=lambda k: pcts[k])
    if pcts[hi] >= 50:
        s = f"{LABEL[hi]}打到同位置前 {max(1, round(100 - pcts[hi]))}%"
    else:
        s = f"八维都在中位以下，相对最好的是{LABEL[hi]}（P{pcts[hi]:.0f}）"
    if pcts[hi] < 50:
        pass
    elif pcts[lo] < 40:
        s += f"，{LABEL[lo]}是短板（P{pcts[lo]:.0f}）"
    else:
        s += "，没有明显短板"
    if other:
        lead = sum(1 for k in pcts if pcts[k] > other[k])
        s += f"；八维里 {lead} 项领先对面"
    return s + "。"


def team_avg(ps: list[dict | None]) -> float | None:
    v = [p["score"] for p in ps if p and p.get("score") is not None]
    return sum(v) / len(v) if v else None
