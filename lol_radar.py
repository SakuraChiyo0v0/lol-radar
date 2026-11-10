#!/usr/bin/env python3
"""英雄联盟「能力雷达图」：同位置百分位对比（PIL 手绘，不依赖 matplotlib）。

用法（--mode 不写时：有 --match-id 是 match，否则 duel）：
  # duel：两人最近 N 局、强制同一位置（默认第一个人的主位置）—— 战前看对位强弱
  lol_radar.py --mode duel --players "A#1" "B#2" [--position MID] [--games 20] [--days 30]
  lol_radar.py --players "A#1"                                     # 单人雷达
  # match：单局同位置对比
  lol_radar.py --mode match --match-id HN1_1234567890 --players "名字#TAG"
  # champ：两人只比同一个英雄的最近对局（分位池 = 同位置所有人）
  lol_radar.py --mode champ --champion 伊泽瑞尔 --players "A#1" "B#2"
  # team：5v5 全队战力。--live 从 PC 客户端读当前对局/选人；--match-id 用库里的对局
  lol_radar.py --mode team --live
  lol_radar.py --mode team --match-id HN1_1234567891

八维（全部按同位置百分位归一，50=中位）：
  伤害占比 / 伤害转化 / 经济每分 / 补刀每分 / 参团率 / 视野每分 / KDA / 承伤占比
"""
from __future__ import annotations

import argparse
import bisect
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from lol_card import config as CFG  # noqa: E402  部署相关设置，全部可用环境变量覆盖

METRICS = [
    ("伤害占比", "dmg_share", "{:.1%}"),
    ("伤害转化", "dmg_per_gold", "{:.2f}"),
    ("经济/分", "gold_per_min", "{:.0f}"),
    ("补刀/分", "cs_per_min", "{:.1f}"),
    ("参团率", "kp", "{:.1%}"),
    ("视野/分", "vision_per_min", "{:.2f}"),
    ("KDA", "kda", "{:.2f}"),
    ("承伤占比", "tank_share", "{:.1%}"),
]

POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")
POS_ALIAS = {
    "TOP": "TOP", "上单": "TOP",
    "JUNGLE": "JUNGLE", "JG": "JUNGLE", "JUG": "JUNGLE", "打野": "JUNGLE",
    "MIDDLE": "MIDDLE", "MID": "MIDDLE", "中单": "MIDDLE",
    "BOTTOM": "BOTTOM", "BOT": "BOTTOM", "ADC": "BOTTOM", "下路": "BOTTOM",
    "UTILITY": "UTILITY", "SUP": "UTILITY", "SUPPORT": "UTILITY", "辅助": "UTILITY",
}
POS_CN_SHORT = {"TOP": "上单", "JUNGLE": "打野", "MIDDLE": "中单", "BOTTOM": "下路", "UTILITY": "辅助"}
POS_BADGE = {"TOP": "上单对位", "JUNGLE": "打野对位", "MIDDLE": "中单对位", "BOTTOM": "下路对位",
             "UTILITY": "辅助对位", "ARAM": "大乱斗"}
RE_MATCH_ID = re.compile(r"^[A-Z0-9]+_\d+$")
RE_PUUID = re.compile(r"^[A-Za-z0-9_-]+$")


def norm_position(p: str | None) -> str | None:
    """把 MID/ADC/SUP 等写法归一到库里的 team_position；不认识的直接拒绝（同时防注入）。"""
    if not p:
        return None
    v = POS_ALIAS.get(p.strip().upper()) or POS_ALIAS.get(p.strip())
    if not v:
        raise SystemExit(f"不认识的位置 {p!r}，可用：TOP / JUNGLE(JG) / MID / ADC(BOT) / SUP")
    return v


def sql_str(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


AGG = """
coalesce(avg(dmg::numeric / nullif(team_dmg::numeric, 0)), 0)   as dmg_share,
coalesce(avg(dmg::numeric / nullif(gold::numeric / 1000.0, 0)), 0) as dmg_per_gold,
coalesce(avg(gold / nullif(tp / 60.0, 0)), 0)           as gold_per_min,
coalesce(avg(cs / nullif(tp / 60.0, 0)), 0)             as cs_per_min,
coalesce(avg((k + a)::numeric / nullif(team_kills::numeric, 0)), 0) as kp,
coalesce(avg(vis / nullif(tp / 60.0, 0)), 0)            as vision_per_min,
coalesce(avg((k + a)::numeric / nullif(greatest(d, 1), 0)), 0) as kda,
coalesce(avg(taken::numeric / nullif(team_taken::numeric, 0)), 0) as tank_share
"""

BASE = """
with t as (
  select mp.puuid, coalesce(mp.team_position,'?') as pos,
         mp.damage_to_champ dmg, mp.gold_earned gold, mp.cs cs, mp.vision_score vis,
         mp.kills k, mp.deaths d, mp.assists a, mp.time_played tp,
         mp.damage_taken taken,
         sum(mp.damage_to_champ) over w as team_dmg,
         sum(mp.damage_taken) over w as team_taken,
         sum(mp.kills) over w as team_kills,
         m.game_creation
  from lol.match_participant mp
  join lol.match m on m.match_id = mp.match_id
  where m.game_creation > now() - interval '{days} days' and mp.time_played > 300
    and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')
  window w as (partition by mp.match_id, mp.team_id)
)
"""


ARAM_QUEUES = {450: "ARAM", 2400: "KIWI"}
ARAM_KEYS = ["dmg_share", "dmg_per_gold", "gold_per_min", "cs_per_min", "kp", "dmg_per_min", "kda", "tank_share"]
ARAM_AXES = [("伤害占比", "dmg_share", "{:.1%}"), ("伤害转化", "dmg_per_gold", "{:.0f}"),
             ("经济/分", "gold_per_min", "{:.0f}"), ("补刀/分", "cs_per_min", "{:.1f}"),
             ("参团率", "kp", "{:.0%}"), ("输出/分", "dmg_per_min", "{:.0f}"),
             ("KDA", "kda", "{:.2f}"), ("承伤占比", "tank_share", "{:.1%}")]

AGG_ARAM = """
coalesce(avg(dmg::numeric / nullif(team_dmg::numeric, 0)), 0)   as dmg_share,
coalesce(avg(dmg::numeric / nullif(gold::numeric / 1000.0, 0)), 0) as dmg_per_gold,
coalesce(avg(gold / nullif(tp / 60.0, 0)), 0)           as gold_per_min,
coalesce(avg(cs / nullif(tp / 60.0, 0)), 0)             as cs_per_min,
coalesce(avg((k + a)::numeric / nullif(team_kills::numeric, 0)), 0) as kp,
coalesce(sum(dmg::numeric) / nullif(sum(tp) / 60.0, 0), 0) as dmg_per_min,
coalesce(avg((k + a)::numeric / nullif(greatest(d, 1), 0)), 0) as kda,
coalesce(avg(taken::numeric / nullif(team_taken::numeric, 0)), 0) as tank_share
"""

BASE_ARAM = """
with t as (
  select '{pos}' as pos,
         mp.damage_to_champ dmg, mp.gold_earned gold, mp.cs cs, mp.vision_score vis,
         mp.kills k, mp.deaths d, mp.assists a, mp.time_played tp,
         mp.damage_taken taken, mp.puuid,
         sum(mp.damage_to_champ) over w as team_dmg,
         sum(mp.damage_taken) over w as team_taken,
         sum(mp.kills) over w as team_kills,
         m.game_creation
  from lol.match_participant mp
  join lol.match m on m.match_id = mp.match_id
  where m.game_creation > now() - interval '{days} days' and mp.time_played > 180
    and m.queue_id = {queue}
  window w as (partition by mp.match_id, mp.team_id)
)
"""


def _db_url() -> str:
    if CFG.DB_URL:
        return CFG.DB_URL
    try:
        return CFG.DB_URL_FILE.read_text().strip()
    except OSError:
        raise SystemExit(f"没有数据库连接串：设 LOL_RADAR_DB_URL，或把连接串写进 {CFG.DB_URL_FILE}"
                         "（路径用 LOL_RADAR_DB_URL_FILE 改）")


def psql(sql: str) -> list[list[str]]:
    cp = subprocess.run([*CFG.PSQL_CMD, _db_url(), "-t", "-A", "-F", "\t", "-c", sql],
                        capture_output=True, text=True, timeout=CFG.DB_TIMEOUT)
    if cp.returncode != 0:
        raise SystemExit(f"psql 失败: {cp.stderr[:400]}")
    # 只去行尾换行：用 strip() 会吃掉首字段为空时的前导分隔符，导致整行左移一列（大乱斗没有 team_position 就会踩到）
    return [line.split("\t") for line in cp.stdout.strip("\n").splitlines() if line.strip()]


POOL_DIR = CFG.POOL_DIR
QUANT = CFG.QUANT_FILE
QUANT_TTL = CFG.CACHE_TTL
CACHE_TTL = CFG.CACHE_TTL


class Pool:
    """同位置样本池：每个位置每个指标一列排好序的数，按 (days, pos) 落盘、12 小时有效。

    只读用得到的位置；pct() 用二分取「严格小于」的个数，和原来逐个比较的定义一致。
    """

    def __init__(self, days: int, refresh: bool = False):
        self.days = int(days)
        self.refresh = refresh
        self._cols: dict[str, dict[str, "array"]] = {}

    def _dir(self) -> Path:
        return POOL_DIR / f"d{self.days}"

    def _fresh(self) -> bool:
        import time
        meta = self._dir() / "meta.json"
        try:
            m = json.loads(meta.read_text())
            return (not self.refresh) and time.time() - m["ts"] < CACHE_TTL
        except Exception:
            return False

    def _build(self) -> None:
        """一条 SQL 出全部位置，每个位置每个指标排序后写成二进制 double 数组。"""
        import time
        from array import array
        sql = BASE.format(days=self.days) + f"select pos, {AGG} from t group by puuid, pos"
        cols: dict[str, list[list[float]]] = {}
        keys = [m[1] for m in METRICS]
        for r in psql(sql):
            c = cols.setdefault(r[0], [[] for _ in keys])
            for i, v in enumerate(r[1:]):
                c[i].append(float(v))
        tmp = POOL_DIR / f".d{self.days}.tmp{int(time.time())}"
        tmp.mkdir(parents=True, exist_ok=True)

        # 乱斗系按队列分开建池：450=普通大乱斗(ARAM)，2400=海克斯/乱斗狂欢(KIWI)，两者数据不能混
        for qid, tag in ARAM_QUEUES.items():
            sql_a = BASE_ARAM.format(days=self.days, queue=qid, pos=tag) + f"select pos, {AGG_ARAM} from t group by puuid, pos"
            arr = [[] for _ in ARAM_KEYS]
            for r in psql(sql_a):
                for i, v in enumerate(r[1:]):
                    arr[i].append(float(v))
            if arr[0]:
                cols[tag] = arr

        for pos, lists in cols.items():
            pos_keys = ARAM_KEYS if pos in ARAM_QUEUES.values() else keys
            for k, vals in zip(pos_keys, lists):
                vals.sort()
                with open(tmp / f"{pos}.{k}.f64", "wb") as fh:
                    array("d", vals).tofile(fh)
        (tmp / "meta.json").write_text(json.dumps({"ts": time.time(), "days": self.days,
                                                   "counts": {p: len(v[0]) for p, v in cols.items()}}))
        # 原子换目录：别的进程正在读旧目录也不会读到半份
        dst = self._dir()
        old = POOL_DIR / f".d{self.days}.old{int(time.time())}"
        if dst.exists():
            dst.rename(old)
        tmp.rename(dst)
        if old.exists():
            shutil.rmtree(old, ignore_errors=True)
        self.refresh = False

    def _ensure(self) -> None:
        if not self._fresh():
            self._build()

    def count(self, pos: str) -> int:
        self._ensure()
        try:
            return int(json.loads((self._dir() / "meta.json").read_text())["counts"].get(pos, 0))
        except Exception:
            return 0

    def col(self, pos: str, key: str):
        from array import array
        if pos not in self._cols:
            self._cols[pos] = {}
        if key not in self._cols[pos]:
            self._ensure()
            f = self._dir() / f"{pos}.{key}.f64"
            a = array("d")
            if f.exists():
                with open(f, "rb") as fh:
                    a.frombytes(fh.read())
            self._cols[pos][key] = a
        return self._cols[pos][key]

    def pct(self, pos: str, key: str, value: float) -> float:
        import bisect
        a = self.col(pos, key)
        if not a:
            return 50.0
        return 100.0 * bisect.bisect_left(a, value) / len(a)

    def pcts(self, pos: str, row: dict, keys: list[str] | None = None) -> dict:
        return {k: self.pct(pos, k, row[k]) for k in (keys or [m[1] for m in METRICS])}

    def frac(self, pos: str, key: str, value: float) -> float:
        """半径系数。外圈(1.0R)=P95，溢出线=P99，最多探到 1.25R。

        P0–P95 线性放大到 0–1.0；P95–P99 贴边（视觉满格）；P99 以上溢出。
        """
        a = self.col(pos, key)
        n = len(a)
        if not n:
            return 0.5
        pct = 100.0 * bisect.bisect_left(a, value) / n
        if pct <= 95.0:
            return max(0.04, pct / 95.0)
        if pct <= 99.0:
            return 1.0
        p90, p99 = a[int(0.90 * n)], a[int(0.99 * n)]
        span = (p99 - p90) or max(abs(p99) * 0.02, 1e-9)
        # 刚过 P99 就先跳出去 10%，再按超出幅度加码，最多 1.35R
        return 1.0 + min(0.45, 0.15 + (value - p99) / span * 0.20)

    def fracs(self, pos: str, row: dict, keys: list[str] | None = None) -> dict:
        return {k: self.frac(pos, k, row[k]) for k in (keys or [m[1] for m in METRICS])}


import shutil  # noqa: E402  Pool._build 用


def pool(days: int, refresh: bool = False) -> Pool:
    return Pool(days, refresh)


def resolve(name: str) -> str:
    sql = ("select p.puuid from lol.player p join lol.match_participant mp on mp.puuid=p.puuid "
           f"where p.game_name||'#'||p.tag_line = {sql_str(name)} "
           "group by 1 order by count(*) desc limit 1")
    r = psql(sql)
    if not r:
        raise SystemExit(f"库里找不到 {name}")
    puuid = r[0][0]
    if not RE_PUUID.match(puuid):
        raise SystemExit(f"{name} 的 puuid 格式异常：{puuid!r}")
    return puuid


def player_row(name: str, days: int, games: int, position: str | None,
               champion: str | None = None, puuid: str | None = None, aram: bool | str = False) -> dict:
    """只扫这个玩家自己的对局，再补队伍合计（比全表窗口快一个数量级）。"""
    puuid = puuid or resolve(name)
    pos_filter = f"and mp.team_position = {sql_str(position)}" if position else ""
    if champion:
        pos_filter += f" and mp.champion_name = {sql_str(champion)}"
    qid = {v: k for k, v in ARAM_QUEUES.items()}.get(aram, 450) if aram else None
    extra = ",\n      coalesce(sum(mine.dmg::numeric) / nullif(sum(mine.tp) / 60.0, 0), 0)" if aram else ""
    scope = (f"and mp.time_played > 180 and m.queue_id = {qid}" if aram else
             "and mp.time_played > 300 and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')")
    sql = f"""
    with mine as (
      select mp.match_id, mp.team_id, mp.team_position, mp.damage_to_champ dmg, mp.gold_earned gold,
             mp.cs, mp.vision_score vis, mp.kills k, mp.deaths d, mp.assists a, mp.time_played tp,
             mp.damage_taken taken, m.game_creation
      from lol.match_participant mp
      join lol.match m on m.match_id = mp.match_id
      where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
        {scope}
        {pos_filter}
      order by m.game_creation desc
      limit {int(games)}
    ),
    team as (
      select mp.match_id, mp.team_id,
             sum(mp.damage_to_champ) team_dmg, sum(mp.damage_taken) team_taken, sum(mp.kills) team_kills
      from lol.match_participant mp
      where mp.match_id in (select match_id from mine)
      group by 1,2
    )
    select {f"'{aram}'" if aram else 'mine.team_position'}, count(*),
      coalesce(avg(mine.dmg::numeric / nullif(team.team_dmg::numeric,0)),0),
      coalesce(avg(mine.dmg::numeric / nullif(mine.gold::numeric/1000.0,0)),0),
      coalesce(avg(mine.gold::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg(mine.cs::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg((mine.k+mine.a)::numeric / nullif(team.team_kills::numeric,0)),0),
      coalesce(avg(mine.vis::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg((mine.k+mine.a)::numeric / nullif(greatest(mine.d,1),0)),0),
      coalesce(avg(mine.taken::numeric / nullif(team.team_taken::numeric,0)),0){extra}
    from mine join team on team.match_id = mine.match_id and team.team_id = mine.team_id
    group by 1 order by 2 desc limit 1
    """
    r = psql(sql)
    if not r:
        what = f"位置 {position}" if position else "排位/匹配分路局"
        if champion:
            what += f"、英雄 {champion}"
        raise NoGames(f"{name} 最近 {days} 天没有对局（{what}）")
    row = {"name": name, "puuid": puuid, "pos": r[0][0], "games": int(r[0][1])}
    row.update({k: float(v) for k, v in zip([m[1] for m in METRICS], r[0][2:])})
    if aram and len(r[0]) > 10:
        row["dmg_per_min"] = float(r[0][10])
    return row


class NoGames(SystemExit):
    pass


def main_position(puuid: str, days: int) -> str | None:
    r = psql(f"""select mp.team_position from lol.match_participant mp
                 join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
                   and mp.time_played > 300 and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')
                 group by 1 order by count(*) desc limit 1""")
    return r[0][0] if r else None


def extras(puuid: str, days: int, position: str | None, champion: str | None = None) -> dict:
    """对位卡用的附加信息。profile_icon 列可能还没建（下一轮入库才会 alter），用 to_jsonb 兼容。"""
    if not puuid or not RE_PUUID.match(puuid):
        return {}
    pu = sql_str(puuid)
    out: dict = {}
    r = psql(f"select to_jsonb(p)->>'profile_icon' from lol.player p where p.puuid = {pu}")
    out["profile_icon"] = int(r[0][0]) if r and r[0][0] else None
    r = psql(f"select tier, division, lp from lol.player_rank where puuid = {pu} order by updated_at desc limit 1")
    if r:
        out["rank"] = {"tier": r[0][0], "division": r[0][1], "lp": int(r[0][2] or 0)}
    pos_filter = f"and mp.team_position = {sql_str(position)}" if position else ""
    if champion:
        pos_filter += f" and mp.champion_name = {sql_str(champion)}"
    r = psql(f"""select mp.champion_name, mp.win from lol.match_participant mp
                 join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {pu} and mp.time_played > 300
                   and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY') {pos_filter}
                 order by m.game_creation desc limit 10""")
    out["recent"] = [{"champion": x[0], "win": x[1] == "t"} for x in r]
    r = psql(f"""select mp.champion_name, count(*), sum(case when mp.win then 1 else 0 end)
                 from lol.match_participant mp join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {pu} and m.game_creation > now() - interval '{int(days)} days'
                   and mp.time_played > 300 {pos_filter}
                 group by 1 order by 2 desc limit 4""")
    out["top_champs"] = [{"champion": x[0], "games": int(x[1]), "wins": int(x[2])} for x in r]
    return out


def champ_wins(puuid: str, champ: str, days: int, games: int, position: str | None) -> int:
    pos_filter = f"and mp.team_position = {sql_str(position)}" if position else ""
    r = psql(f"""select count(*) filter (where win) from (
                   select mp.win from lol.match_participant mp join lol.match m on m.match_id = mp.match_id
                   where mp.puuid = {sql_str(puuid)} and mp.champion_name = {sql_str(champ)}
                     and m.game_creation > now() - interval '{int(days)} days' and mp.time_played > 300 {pos_filter}
                   order by m.game_creation desc limit {int(games)}) x""")
    return int(r[0][0]) if r else 0


def avg_kda_line(puuid: str, days: int, games: int, position: str | None, champion: str | None = None) -> str:
    pos_filter = f"and mp.team_position = {sql_str(position)}" if position else ""
    if champion:
        pos_filter += f" and mp.champion_name = {sql_str(champion)}"
    r = psql(f"""select avg(k), avg(d), avg(a) from (
                   select mp.kills k, mp.deaths d, mp.assists a from lol.match_participant mp
                   join lol.match m on m.match_id = mp.match_id
                   where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
                     and mp.time_played > 300
                     and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY') {pos_filter}
                   order by m.game_creation desc limit {int(games)}) x""")
    if not r or not r[0][0]:
        return ""
    k, d, a = (float(x) for x in r[0])
    return f"{k:.1f} / {d:.1f} / {a:.1f}"


METRIC_EXPR = {
    "dmg_per_min": "dmg::numeric / nullif(tp / 60.0, 0)",
    "dmg_share": "dmg::numeric / nullif(team_dmg::numeric, 0)",
    "dmg_per_gold": "dmg::numeric / nullif(gold::numeric / 1000.0, 0)",
    "gold_per_min": "gold::numeric / nullif(tp / 60.0, 0)",
    "cs_per_min": "cs::numeric / nullif(tp / 60.0, 0)",
    "kp": "(k + a)::numeric / nullif(team_kills::numeric, 0)",
    "vision_per_min": "vis::numeric / nullif(tp / 60.0, 0)",
    "kda": "(k + a)::numeric / nullif(greatest(d, 1), 0)",
    "tank_share": "taken::numeric / nullif(team_taken::numeric, 0)",
}


def quantiles(days: int, refresh: bool = False) -> tuple[dict, dict]:
    """按位置 × 指标取 P1..P99 分位曲线（缓存 12h），用于单局百分位。

    返回 (curves, counts)：counts 是每个位置参与分位计算的单局样本数（人·局）。
    """
    import time
    try:
        if not refresh:
            d = json.loads(QUANT.read_text())
            # 旧缓存没有 counts，视为失效重建
            if d.get("days") == days and time.time() - d.get("ts", 0) < QUANT_TTL and "counts" in d:
                return d["curves"], d["counts"]
    except Exception:
        pass

    keys = [m[1] for m in METRICS]
    inner = ", ".join(f"{expr} as {k}" for k, expr in METRIC_EXPR.items())
    cols = ", ".join(
        f"percentile_cont(array(select generate_series(1,99)/100.0)) within group (order by {k})"
        for k in keys)
    sql = (BASE.format(days=int(days))
           + f", m as (select pos, {inner} from t) select pos, count(*), {cols} from m group by pos")
    curves: dict[str, dict[str, list[float]]] = {}
    counts: dict[str, int] = {}
    for r in psql(sql):
        pos = r[0]
        counts[pos] = int(r[1])
        curves[pos] = {}
        for i, k in enumerate(keys):
            raw = r[2 + i].strip("{}").split(",")
            curves[pos][k] = [float(x) for x in raw]
    # 大乱斗/斗魂的分位曲线（不分位置，全池）
    inner_a = ", ".join(f"{METRIC_EXPR[k]} as {k}" for k in ARAM_KEYS)
    cols_a = ", ".join(
        f"percentile_cont(array(select generate_series(1,99)/100.0)) within group (order by {k})"
        for k in ARAM_KEYS)
    for qid, tag in ARAM_QUEUES.items():
        sql_a = (BASE_ARAM.format(days=int(days), queue=qid, pos=tag)
                 + f", m as (select {inner_a} from t) select count(*), {cols_a} from m")
        for r in psql(sql_a):
            counts[tag] = int(r[0])
            curves[tag] = {}
            for i, k in enumerate(ARAM_KEYS):
                raw = r[1 + i].strip("{}").split(",")
                curves[tag][k] = [float(x) for x in raw]

    try:
        QUANT.parent.mkdir(parents=True, exist_ok=True)
        QUANT.write_text(json.dumps({"ts": time.time(), "days": days, "curves": curves, "counts": counts}))
    except Exception:
        pass
    return curves, counts


def pct_from_curve(value: float, curve: list[float]) -> float:
    """把数值映射到 P1..P99 曲线上的百分位（线性插值）。"""
    if not curve:
        return 50.0
    if value <= curve[0]:
        return 1.0
    if value >= curve[-1]:
        return 99.0
    for i in range(len(curve) - 1):
        lo, hi = curve[i], curve[i + 1]
        if lo <= value <= hi:
            frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
            return 1.0 + (i + frac)
    return 50.0


def match_rows(match_id: str) -> list[dict]:
    """取一局里所有参与者 + 队伍合计，供单局雷达用。match_id 必须已过格式校验。"""
    mid = sql_str(match_id)
    sql = f"""
    with team as (
      select team_id, sum(damage_to_champ) team_dmg, sum(damage_taken) team_taken, sum(kills) team_kills
      from lol.match_participant where match_id = {mid} group by 1
    )
    select coalesce(mp.team_position,''), coalesce(mp.champion_name,''), mp.kills, mp.deaths, mp.assists, mp.cs, mp.gold_earned,
           mp.damage_to_champ, mp.damage_taken, mp.vision_score, mp.time_played, mp.win,
           team.team_dmg, team.team_taken, team.team_kills,
           coalesce(p.game_name || '#' || p.tag_line,'?'), mp.team_id,
           mp.puuid, coalesce(mp.champ_level,0), coalesce(array_to_string(mp.items, ','),''),
           coalesce(mp.spell1_id,0), coalesce(mp.spell2_id,0),
           coalesce(mp.perks->'styles'->0->'selections'->0->>'perk',''), coalesce(mp.perks->'styles'->1->>'style',''),
           m.queue_id
    from lol.match_participant mp
    join lol.match m on m.match_id = mp.match_id
    join team on team.team_id = mp.team_id
    left join lol.player p on p.puuid = mp.puuid
    where mp.match_id = {mid}
    """
    out = []
    for r in psql(sql):
        tp = float(r[10] or 0) or 1
        out.append({
            "pos": r[0] or "?", "champ": r[1], "name": r[15] or "?",
            "k": float(r[2] or 0), "d": float(r[3] or 0), "a": float(r[4] or 0),
            "cs": float(r[5] or 0), "gold": float(r[6] or 0), "dmg": float(r[7] or 0),
            "taken": float(r[8] or 0), "vis": float(r[9] or 0), "tp": tp, "win": r[11],
            "team_dmg": float(r[12] or 0), "team_taken": float(r[13] or 0), "team_kills": float(r[14] or 0),
            "team": r[16], "puuid": r[17], "champ_level": int(r[18] or 0),
            # items 固定 7 格：前 6 格是装备槽（保留空槽 0，位置不乱），第 7 格是饰品
            "items": ([int(x or 0) for x in (r[19] or "").split(",")] + [0] * 7)[:6],
            "trinket": ([int(x or 0) for x in (r[19] or "").split(",")] + [0] * 7)[6],
            "spells": [int(x) for x in (r[20], r[21]) if x],
            "keystone": int(r[22]) if r[22] else None, "sub_style": int(r[23]) if r[23] else None,
            "queue": int(r[24]) if len(r) > 24 and r[24] else None,
        })
    return out


def pick_pair(rows: list[dict], players: list[str] | None, position: str | None) -> tuple[str, list[dict]]:
    """单局模式选人：先按名字找人、用他的位置；没给名字才按 --position 取该位置双方。"""
    if players:
        full = set(players)
        short = {n.split("#")[0] for n in players}
        hit = [r for r in rows if r["name"] in full]
        if len(hit) < len(players):  # 名字没带 tag 或 tag 对不上时，退到只比 game_name
            hit = [r for r in rows if r["name"] in full or r["name"].split("#")[0] in short]
        if not hit:
            raise SystemExit(f"这一局里找不到 {', '.join(players)}")
        if len(hit) >= 2:
            pair = hit[:2]
            pos = pair[0]["pos"]
            if pair[1]["pos"] != pos:
                print(f"注意：两人位置不同（{pos} / {pair[1]['pos']}），跨位置比较仅供参考", file=sys.stderr)
            return pos, pair
        me = hit[0]
        pos = me["pos"]
        opp = [r for r in rows if r["pos"] == pos and r["team"] != me["team"]]
        return pos, [me] + opp[:1]
    if not position:
        have = sorted({r["pos"] for r in rows if r["pos"] in POSITIONS})
        raise SystemExit(f"单局模式需要 --players 或 --position（本局位置：{' / '.join(have)}）")
    pair = [r for r in rows if r["pos"] == position]
    if not pair:
        raise SystemExit(f"这一局没有 {position} 位置的玩家")
    pair.sort(key=lambda r: r["dmg"], reverse=True)
    return position, pair[:2]


def game_metrics(r: dict) -> dict:
    mins = max(r["tp"] / 60, 1)
    return {
        "dmg_share": r["dmg"] / max(r["team_dmg"], 1),
        "dmg_per_gold": r["dmg"] / max(r["gold"] / 1000, 1),
        "gold_per_min": r["gold"] / mins,
        "cs_per_min": r["cs"] / mins,
        "kp": (r["k"] + r["a"]) / max(r["team_kills"], 1),
        "vision_per_min": r["vis"] / mins,
        "kda": (r["k"] + r["a"]) / max(r["d"], 1),
        "tank_share": r["taken"] / max(r["team_taken"], 1),
    }


def pct(value: float, values: list[float]) -> float:
    if not values:
        return 50.0
    return 100.0 * sum(1 for v in values if v < value) / len(values)


LIVE_SCRIPT = str(Path(__file__).resolve().parent / "lol_live_lobby.py")


def live_lobby() -> dict:
    """scp 取人脚本到装着客户端的 Windows 机器跑一次（只读 LCU），回来是 10 个人。
    主机、ssh 配置、远端目录都由 LOL_RADAR_LIVE_* 环境变量决定。"""
    import time
    host = CFG.LIVE_SSH_HOST
    remote = f"{CFG.LIVE_REMOTE_DIR.rstrip('/')}/lol_live_lobby_{int(time.time())}.py"
    ssh = (["-F", CFG.LIVE_SSH_CONFIG] if CFG.LIVE_SSH_CONFIG else []) + [
        "-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]
    cp = subprocess.run(["scp", "-q", *ssh, LIVE_SCRIPT, f"{host}:{remote}"], capture_output=True, text=True, timeout=30)
    if cp.returncode != 0:
        raise SystemExit(f"连不上 {host}：{cp.stderr.strip()[:200]}")
    try:
        cp = subprocess.run(["ssh", "-n", *ssh, host, f"{CFG.LIVE_REMOTE_PYTHON} {remote}"],
                            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    finally:
        subprocess.run(["ssh", "-n", *ssh, host, f"del {remote.replace('/', chr(92))}"],
                       capture_output=True, timeout=20)
    try:
        return json.loads(cp.stdout.strip().splitlines()[-1])
    except Exception:
        raise SystemExit(f"读客户端失败：{(cp.stdout or cp.stderr)[-300:]}")


def team_player(entry: dict, pos: str, a, pool_rows: list[dict], champ_names: dict) -> dict:
    """team 模式里一个人：按这个位置算近期分；本位置没记录就按全位置算并标出来。"""
    from lol_radar_card import score
    puuid, name = entry.get("puuid") or "", entry.get("name") or ""
    champ = champ_names.get(entry.get("champion_id")) or entry.get("champion")
    if not puuid or not RE_PUUID.match(puuid):
        return {"missing_player": True, "pos": pos, "name": name or "未知玩家",
                "why": "选人阶段看不到对面" if entry.get("hidden") else "看不到是谁"}
    base = {"name": name or "?", "puuid": puuid, "pos": pos, "champion": champ}
    st, fallback = None, False
    try:
        st = player_row(name, a.days, a.games, pos, puuid=puuid)
    except NoGames:
        try:
            st = player_row(name, a.days, a.games, None, puuid=puuid)
            fallback = True
        except NoGames:
            pass
    ex = extras(puuid, a.days, None if fallback else pos)
    ex.pop("top_champs", None)
    if not st:
        return {**base, **ex, "score": None, "pcts": {}, "games": 0}
    pc = pool_rows.pcts(st["pos"], st)
    return {**base, **ex, "pos": st["pos"], "games": st["games"], "pcts": pc,
            "score": score(pc, st["pos"]), "fallback": fallback,
            "low_sample": st["games"] < a.min_games}


def team_mode(a) -> int:
    from lol_radar_card import Assets, TEAM_ORDER, render_team
    A = Assets()
    champ_names = {}
    for k in A._champ_data():
        cid = A._champ_data()[k].get("key")
        champ_names[int(cid)] = k
    if a.live:
        lob = live_lobby()
        if lob.get("error"):
            raise SystemExit(lob["error"])
        if not lob.get("ally"):
            raise SystemExit(f"客户端当前不在选人或对局中（阶段：{lob.get('phase')}）")
        ally, enemy = lob["ally"], lob["enemy"]
        for e in enemy:
            e["hidden"] = lob.get("source") == "champ-select"
        src = {"gameflow": "对局进行中", "champ-select": "选人阶段"}.get(lob.get("source"), lob.get("phase"))
        sides = ("我方", "敌方")
        sub_head = f"客户端实时 · {src}"
    else:
        if not a.match_id or not RE_MATCH_ID.match(a.match_id):
            raise SystemExit("team 模式需要 --live 或合法的 --match-id")
        rows = match_rows(a.match_id)
        if not rows:
            raise SystemExit(f"找不到对局 {a.match_id}")
        teams = sorted({r["team"] for r in rows})
        conv = lambda t: [{"puuid": r["puuid"], "name": r["name"], "pos": r["pos"], "champion": r["champ"]}
                          for r in rows if r["team"] == t]
        ally, enemy = conv(teams[0]), conv(teams[-1])
        if a.players:
            want = {n.split("#")[0] for n in a.players}
            if any(e["name"].split("#")[0] in want for e in enemy):
                ally, enemy = enemy, ally
        sides = ("蓝色方", "红色方") if not a.players else ("我方", "敌方")
        sub_head = f"{a.match_id} 的十人 · 按各自近期数据（含本局）"

    def order(team):
        by_pos = {e.get("pos"): e for e in team if e.get("pos") in TEAM_ORDER}
        rest = [e for e in team if e.get("pos") not in TEAM_ORDER or by_pos.get(e.get("pos")) is not e]
        out = []
        for pos in TEAM_ORDER:
            out.append((pos, by_pos.get(pos) or (rest.pop(0) if rest else {})))
        return out

    pool_rows = pool(a.pool_days, refresh=a.refresh)
    pool_rows.count("TOP")  # 先在主线程把池子备好，免得 10 个线程同时冷启动重建
    jobs = order(ally) + order(enemy)
    res = pmap(lambda pe: team_player(pe[1], pe[0], a, pool_rows, champ_names), jobs, workers=10)
    left, right = res[:5], res[5:]
    hidden = sum(1 for p in right if p.get("missing_player"))
    render_team(left, right, Path(a.out), {
        "title": "战前战力 · 5v5 对位",
        "subtitle": f"{sub_head} · 每人取最近 {a.days} 天本位置 {a.games} 局"
                    + (f" · 分位池近 {a.pool_days} 天" if a.pool_days != a.days else ""),
        "badge": "全队对比", "sides": sides,
        "note": (f"对面 {hidden} 人暂不可见，全队分只算看得到的人" if hidden else
                 "全队分 = 五人评分平均 · 每路差值 ≥0.3 才算领先"),
        "footer": "评分 = 八维分位按位置加权（0–10） · 红字 = 本位置样本少或没有记录 · 数据：lol.* / 图标 ddragon",
    })
    print(json.dumps({"out": a.out, "mode": "team", "left": [
        {"name": p.get("name"), "pos": p.get("pos"), "score": p.get("score")} for p in left], "right": [
        {"name": p.get("name"), "pos": p.get("pos"), "score": p.get("score")} for p in right]},
        ensure_ascii=False))
    return 0


def pmap(fn, items, workers: int = 6):
    """按顺序返回结果的并发 map；子任务抛 SystemExit 照样往外抛。"""
    from concurrent.futures import ThreadPoolExecutor
    items = list(items)
    if len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=min(workers, len(items))) as ex:
        return list(ex.map(fn, items))


def player_pos_rows(puuid: str, days: int, games: int) -> list[dict]:
    """这个人最近 N 局（不限位置），按位置拆开各算一组八维均值。SQL 与 player_row 同口径。"""
    sql = f"""
    with mine as (
      select mp.match_id, mp.team_id, mp.team_position, mp.damage_to_champ dmg, mp.gold_earned gold,
             mp.cs, mp.vision_score vis, mp.kills k, mp.deaths d, mp.assists a, mp.time_played tp,
             mp.damage_taken taken, m.game_creation
      from lol.match_participant mp
      join lol.match m on m.match_id = mp.match_id
      where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
        and mp.time_played > 300
        and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')
      order by m.game_creation desc
      limit {int(games)}
    ),
    team as (
      select mp.match_id, mp.team_id,
             sum(mp.damage_to_champ) team_dmg, sum(mp.damage_taken) team_taken, sum(mp.kills) team_kills
      from lol.match_participant mp
      where mp.match_id in (select match_id from mine)
      group by 1,2
    )
    select mine.team_position, count(*),
      coalesce(avg(mine.dmg::numeric / nullif(team.team_dmg::numeric,0)),0),
      coalesce(avg(mine.dmg::numeric / nullif(mine.gold::numeric/1000.0,0)),0),
      coalesce(avg(mine.gold::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg(mine.cs::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg((mine.k+mine.a)::numeric / nullif(team.team_kills::numeric,0)),0),
      coalesce(avg(mine.vis::numeric / nullif(mine.tp/60.0,0)),0),
      coalesce(avg((mine.k+mine.a)::numeric / nullif(greatest(mine.d,1),0)),0),
      coalesce(avg(mine.taken::numeric / nullif(team.team_taken::numeric,0)),0)
    from mine join team on team.match_id = mine.match_id and team.team_id = mine.team_id
    group by 1 order by 2 desc
    """
    keys = [m[1] for m in METRICS]
    out = []
    for row in psql(sql):
        d = {"pos": row[0], "games": int(row[1])}
        d.update({k: float(v) for k, v in zip(keys, row[2:])})
        out.append(d)
    return out


def overall_mode(a) -> int:
    """整体实力：每个位置先和本位置的人比（分位 + 评分），再按局数加权成一个不分路的总分。"""
    from lol_radar_card import render, score
    keys = [m[1] for m in METRICS]
    pool_rows = pool(a.pool_days, refresh=a.refresh)
    pool_rows.count("TOP")  # 主线程先备好池子，别让两个线程同时冷启动
    puuids = pmap(resolve, a.players)

    def build(np_):
        name, pu = np_
        rows = player_pos_rows(pu, a.days, a.games)
        if not rows:
            raise NoGames(f"{name} 最近 {a.days} 天没有分路对局")
        tot = sum(x["games"] for x in rows)
        per = []
        for x in rows:
            pc = pool_rows.pcts(x["pos"], x)
            per.append({"pos": x["pos"], "games": x["games"], "pcts": pc, "score": score(pc, x["pos"])})
        pcts = {k: sum(p["pcts"][k] * p["games"] for p in per) / tot for k in keys}
        stats = {k: sum(x[k] * x["games"] for x in rows) / tot for k in keys}
        sc = round(sum(p["score"] * p["games"] for p in per) / tot, 1)
        ex = extras(pu, a.days, None)
        return {
            **ex, "name": name, "puuid": pu, "pos": "ALL", "games": tot,
            "stats": stats, "pcts": pcts, "score": sc, "pos_breakdown": per,
            "pool_note": f"最近 {tot} 局 · {len(per)} 个位置", "low_sample": tot < a.min_games,
            "champion": (ex.get("top_champs") or [{}])[0].get("champion"),
            "kda_line": avg_kda_line(pu, a.days, a.games, None),
        }

    cards = pmap(build, list(zip(a.players, puuids)))
    render(cards, Path(a.out), {
        "title": "整体实力 · 全位置" if len(cards) == 2 else "整体实力 · 全位置雷达",
        "subtitle": f"最近 {a.days} 天 · 每人取最近 {a.games} 局（不分位置）"
                    f" · 每个位置先和本位置的人比，再按局数加权 · 分位池近 {a.pool_days} 天",
        "badge": "全位置",
        "footer": "评分 = 各位置评分按局数加权（0–10） · 轴上原始值为各位置按局数加权平均 · 数据：lol.* / 图标 ddragon",
    })
    print(json.dumps({"out": a.out, "mode": "overall", "players": [
        {"name": c["name"], "games": c["games"], "score": c["score"],
         "by_pos": [{"pos": p["pos"], "games": p["games"], "score": p["score"]} for p in c["pos_breakdown"]]}
        for c in cards]}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--players", nargs="+", default=None)
    ap.add_argument("--games", type=int, default=None, help="每人最多取多少局（默认 20；overall 默认 50）")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--position", default=None, help="TOP / JUNGLE(JG) / MID / ADC(BOT) / SUP")
    ap.add_argument("--out", default=str(CFG.DEFAULT_OUT), help="出图路径（默认 LOL_RADAR_OUT）")
    ap.add_argument("--refresh", action="store_true", help="强制重建样本池（默认缓存 12 小时）")
    ap.add_argument("--match-id", default=None, help="单局模式：对比这一局同位置的两个人")
    ap.add_argument("--mode", choices=["duel", "match", "champ", "team", "overall", "aram"], default=None,
                    help="duel=两人同位置近期；match=单局；champ=同英雄；team=5v5 全队；overall=整体实力；aram=大乱斗/斗魂")
    ap.add_argument("--champion", default=None, help="champ 模式：英雄（英文 id / 中文名 / 称号）")
    ap.add_argument("--live", action="store_true", help="team 模式：从 PC 客户端读当前对局/选人")
    ap.add_argument("--pool-days", type=int, default=None,
                    help="同位置池子的时间窗（默认 = min(--days, 30)）。--days 只管取个人对局")
    ap.add_argument("--min-games", type=int, default=5, help="少于这么多局在图上标红提示样本不足")
    ap.add_argument("--aram-queue", choices=["aram", "kiwi"], default="aram",
                    help="aram 模式用哪个池：aram=普通大乱斗(450)，kiwi=海克斯乱斗(2400)")
    a = ap.parse_args()
    a.mode = a.mode or ("match" if a.match_id else "duel")
    a.games = a.games or (50 if a.mode == "overall" else 20)
    a.pool_days = a.pool_days or min(a.days, 30)
    if not (1 <= a.pool_days <= 365):
        ap.error("--pool-days 取 1–365")
    if a.mode == "team":
        return team_mode(a)
    if a.mode == "champ" and not (a.champion and a.players):
        ap.error("champ 模式需要 --champion 和 --players")
    if a.mode == "match" and not a.match_id:
        ap.error("match 模式需要 --match-id")
    if not a.match_id and not a.players:
        ap.error("需要 --players（历史模式）或 --match-id（单局模式）")
    if not (1 <= a.days <= 365) or not (1 <= a.games <= 500):
        ap.error("--days 取 1–365，--games 取 1–500")
    if a.players and len(a.players) > 2:
        ap.error("--players 最多两个")
    position = norm_position(a.position)

    if a.mode == "overall":
        return overall_mode(a)

    if a.mode == "aram":
        tag = "KIWI" if a.aram_queue == "kiwi" else "ARAM"
        pool_rows = pool(a.pool_days, refresh=a.refresh)
        puuids = pmap(resolve, a.players)
        stats = pmap(lambda np_: player_row(np_[0], a.days, a.games, None, puuid=np_[1], aram=tag),
                     list(zip(a.players, puuids)))
        pcts = [pool_rows.pcts(tag, st, ARAM_KEYS) for st in stats]
        notes = [f"{'海克斯乱斗' if tag == 'KIWI' else '大乱斗'}池 {pool_rows.count(tag)} 人" for _ in stats]
        from lol_radar_card import render
        cards = []
        for st, pc, note in zip(stats, pcts, notes):
            low = st["games"] < a.min_games
            ex = extras(st["puuid"], a.days, None)
            ex.pop("top_champs", None)
            cards.append({
                **ex, "name": st["name"], "pos": "ARAM", "games": st["games"],
                "pool_note": note, "low_sample": low,
                "stats": {k: st[k] for k in pc}, "pcts": pc, "axes": ARAM_AXES,
                "fracs": pool_rows.fracs(tag, st, ARAM_KEYS),
            })
        render(cards, Path(a.out), {
            "title": ("海克斯乱斗 · 同模式百分位" if tag == "KIWI" else "大乱斗 · 同模式百分位"),
            "subtitle": f"最近 {a.days} 天 · 每人取最近 {a.games} 局 · 分位池：近 {a.pool_days} 天同模式",
            "badge": "海克斯乱斗" if tag == "KIWI" else "大乱斗",
            "footer": "外圈 = 同模式 P95（满格） · 超过 P99 才溢出",
        })
        print(json.dumps({"out": a.out, "mode": "aram", "players": [
            {"name": s["name"], "queue": a.aram_queue, "games": s["games"],
             "pct": {k: round(v, 1) for k, v in pc.items()}}
            for s, pc in zip(stats, pcts)]}, ensure_ascii=False))
        return 0

    if a.mode == "match":
        if not RE_MATCH_ID.match(a.match_id):
            raise SystemExit(f"match-id 格式不对：{a.match_id!r}（应形如 HN1_1234567890）")
        curves, counts = quantiles(a.days, refresh=a.refresh)
        rows = match_rows(a.match_id)
        if not rows:
            raise SystemExit(f"找不到对局 {a.match_id}")
        queue = rows[0].get("queue")
        aram_tag = ARAM_QUEUES.get(queue)
        aram_q = aram_tag is not None
        if not aram_q and all(r["pos"] not in POSITIONS for r in rows):
            raise SystemExit(f"这一局（queue {queue}）没有分路数据，既不是峡谷也不是大乱斗/斗魂，暂不支持")
        if aram_q:
            # 大乱斗/斗魂：没有分路，按伤害挑人（给了名字就按名字）
            pos = aram_tag
            for r in rows:
                r["pos"] = aram_tag
            if a.players:
                short = {n.split("#")[0] for n in a.players}
                pair = [r for r in rows if r["name"] in set(a.players) or r["name"].split("#")[0] in short]
                if not pair:
                    raise SystemExit(f"这一局里找不到 {', '.join(a.players)}")
            else:
                pair = sorted(rows, key=lambda r: r["dmg"], reverse=True)
            pair = pair[:2]
        else:
            pos, pair = pick_pair(rows, a.players, position)
        stats, pcts, notes, fracs = [], [], [], []
        for r in pair:
            gm = game_metrics(r)
            if pos in ARAM_QUEUES.values():
                mins = max(r["tp"] / 60, 1)
                gm = {k: gm[k] for k in ARAM_KEYS if k in gm} | {"dmg_per_min": r["dmg"] / mins}
            curve = curves.get(r["pos"], {})
            pc = {k: pct_from_curve(gm[k], curve.get(k, [])) for k in gm}
            stats.append({"name": f"{r['name']} · {r['champ']}", "pos": r["pos"], "games": 1, **gm})
            pcts.append(pc)
            fcs = {}
            for k in gm:
                cv = curve.get(k, [])
                p = pc[k]
                if len(cv) >= 99:
                    p90, p99 = cv[89], cv[98]
                    v = gm[k]
                    if v > p99:
                        span = (p99 - p90) or max(abs(p99) * 0.02, 1e-9)
                        fcs[k] = 1.0 + min(0.45, 0.15 + (v - p99) / span * 0.20)
                        continue
                fcs[k] = max(0.04, p / 95.0) if p <= 95 else 1.0
            fracs.append(fcs)
            notes.append(f"同位置样本 {counts.get(r['pos'], 0)} 局")
        from lol_radar_card import render
        exs = pmap(lambda r: extras(r.get("puuid"), a.days, r["pos"]), pair)
        cards = []
        for r, st, pc, note, ex, fc in zip(pair, stats, pcts, notes, exs, fracs):
            ex.pop("top_champs", None)
            cards.append({
                **ex, "name": r["name"], "pos": r["pos"], "games": 1, "pool_note": note,
                "stats": {k: st[k] for k in pc}, "pcts": pc, "fracs": fc,
                **({"axes": ARAM_AXES} if pos in ARAM_QUEUES.values() else {}), "champion": r["champ"],
                "champ_level": r["champ_level"], "items": r["items"], "trinket": r.get("trinket"),
                "spells": r["spells"],
                "keystone": r["keystone"], "sub_style": r["sub_style"], "win": r["win"] == "t",
                "kda_line": f"{r['k']:.0f} / {r['d']:.0f} / {r['a']:.0f}",
            })
        render(cards, Path(a.out), {
            "title": "单局对位 · 同位置百分位",
            "subtitle": f"{a.match_id} · 对局时长 {pair[0]['tp'] / 60:.0f} 分钟 · 分位池：最近 {a.days} 天同位置单局",
            "badge": POS_BADGE.get(pos, pos),
            "footer": "外圈 = 同位置 P95（满格） · 超过 P99 才溢出 · 评分 = 八维分位按位置加权（0–10）",
        })
        print(json.dumps({"out": a.out, "match": a.match_id, "pos": pos, "players": [
            {"name": s["name"], "pct": {k: round(v, 1) for k, v in pc.items()}} for s, pc in zip(stats, pcts)]},
            ensure_ascii=False))
        return 0

    champ_key = champ_cn = None
    if a.mode == "champ":
        from lol_radar_card import Assets
        hit = Assets().resolve_champion(a.champion)
        if not hit:
            raise SystemExit(f"不认识的英雄 {a.champion!r}")
        _, champ_key, champ_cn = hit

    pool_rows = pool(a.pool_days, refresh=a.refresh)
    puuids = pmap(resolve, a.players)
    if len(a.players) == 2 and not position:
        # 两人对比默认锁同一位置：第一个人的主位置（champ 模式取他玩这个英雄最多的位置）
        if champ_key:
            r = psql(f"""select mp.team_position from lol.match_participant mp
                         join lol.match m on m.match_id = mp.match_id
                         where mp.puuid = {sql_str(puuids[0])} and mp.champion_name = {sql_str(champ_key)}
                           and m.game_creation > now() - interval '{int(a.days)} days' and mp.time_played > 300
                           and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')
                         group by 1 order by count(*) desc limit 1""")
            position = r[0][0] if r else None
        else:
            position = main_position(puuids[0], a.days)
    stats = pmap(lambda np_: player_row(np_[0], a.days, a.games, position, champion=champ_key, puuid=np_[1]),
                 list(zip(a.players, puuids)))
    pcts = [pool_rows.pcts(st["pos"], st) for st in stats]
    notes = [f"同位置样本 {pool_rows.count(st['pos'])} 人" for st in stats]

    from lol_radar_card import render
    def side(st):
        ex = extras(st["puuid"], a.days, position or st["pos"], champ_key)
        if champ_key:
            ex["top_champs"] = [{"champion": champ_key, "games": st["games"],
                                 "wins": champ_wins(st["puuid"], champ_key, a.days, a.games, st["pos"])}]
        return ex, avg_kda_line(st["puuid"], a.days, a.games, position or st["pos"], champ_key)
    sides = pmap(side, stats)
    cards = []
    for st, pc, note, (ex, kda_line) in zip(stats, pcts, notes, sides):
        low = st["games"] < a.min_games
        cards.append({
            **ex, "name": st["name"], "pos": st["pos"], "games": st["games"],
            "pool_note": f"最近 {st['games']} 局{'（样本少）' if low else ''} · {note}", "low_sample": low,
            "stats": {k: st[k] for k in pc}, "pcts": pc,
            "fracs": pool_rows.fracs(st["pos"], st),
            "champion": champ_key or (ex.get("top_champs") or [{}])[0].get("champion"),
            "kda_line": kda_line,
        })
    same = len({c["pos"] for c in cards}) == 1
    if champ_key:
        title = f"英雄专精 · {champ_cn}"
        sub = (f"最近 {a.days} 天 · 只算 {champ_cn} 的对局，每人最多 {a.games} 局"
               f" · 分位池：近 {a.pool_days} 天同位置所有英雄")
    else:
        title = "战前对位 · 同位置百分位" if len(cards) == 2 else "能力雷达 · 同位置百分位"
        sub = f"最近 {a.days} 天 · 每人取最近 {a.games} 局" + (
            f" · 统一按{POS_CN_SHORT.get(cards[0]['pos'])}比" if len(cards) == 2 and same else "")
        if a.pool_days != a.days:
            sub += f" · 分位池：近 {a.pool_days} 天"
    if not same:
        sub += " · 位置不同，跨位置比较仅供参考"
    render(cards, Path(a.out), {
        "title": title,
        "subtitle": sub,
        "badge": ("跨位置" if not same else
                  POS_BADGE.get(cards[0]["pos"], cards[0]["pos"]) if len(cards) == 2 else
                  POS_CN_SHORT.get(cards[0]["pos"], cards[0]["pos"])),
        "footer": "外圈 = 同位置 P95（满格） · 超过 P99 才溢出 · 评分 = 八维分位按位置加权（0–10）",
    })
    print(json.dumps({"out": a.out, "players": [
        {"name": s["name"], "pos": s["pos"], "games": s["games"],
         "pct": {k: round(v, 1) for k, v in pc.items()}} for s, pc in zip(stats, pcts)]},
        ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
