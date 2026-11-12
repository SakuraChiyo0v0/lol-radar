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
  # recap：对局清算，一局十个人的本局能力（峡谷按分路对位，乱斗按评分配对）
  lol_radar.py --mode recap --match-id HN1_1234567891

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
from dataclasses import dataclass
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
    and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY'){qf}
  window w as (partition by mp.match_id, mp.team_id)
)
"""


ARAM_QUEUES = {450: "ARAM", 2400: "KIWI"}
SMALL_POOL = 200   # 比较池少于这么多人时，图上提示百分位不稳


@dataclass(frozen=True)
class Queue:
    """「和谁比」：取哪些对局、比较池用哪些人。图怎么排由模板（--mode）决定，两者自由组合。"""
    key: str            # 命令行取值
    name: str           # 中文名，上图
    ids: tuple = ()     # queue_id；空 = 不限队列（峡谷所有分路局）
    laned: bool = True  # 有分路：比较池按位置分；没有分路（乱斗）：整个队列一个池
    tag: str = ""       # 没有分路的队列在比较池里的键

    def filter(self) -> str:
        return f" and m.queue_id in ({', '.join(str(int(i)) for i in self.ids)})" if self.ids else ""


QUEUES = {q.key: q for q in (
    Queue("rift", "峡谷"),                                        # 默认：单双排 + 灵活 + 匹配，和原来一致
    Queue("solo", "单双排", (420,)),
    Queue("flex", "灵活排位", (440,)),
    Queue("ranked", "排位", (420, 440)),
    Queue("normal", "匹配", (400, 430, 480, 490)),
    Queue("aram", "大乱斗", (450,), laned=False, tag="ARAM"),
    Queue("kiwi", "海克斯乱斗", (2400,), laned=False, tag="KIWI"),
)}
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
    """同位置样本池：每个位置每个指标一列排好序的数，按 (队列, days, pos) 落盘、12 小时有效。

    默认队列 rift（峡谷全部分路局）的池子里还顺带建了 ARAM / KIWI 两个乱斗池，目录名沿用 d{days}，
    和原来的缓存兼容；其余峡谷队列（solo / flex / ranked / normal）各自一个目录 {key}-d{days}，只含五个位置。
    只读用得到的位置；pct() 用二分取「严格小于」的个数，和原来逐个比较的定义一致。
    """

    def __init__(self, days: int, refresh: bool = False, queue: Queue | None = None):
        self.days = int(days)
        self.refresh = refresh
        # 乱斗队列的池子本来就建在 rift 池里，按 tag 取
        self.queue = queue if queue is not None and queue.laned else QUEUES["rift"]
        self._cols: dict[str, dict[str, "array"]] = {}

    def _dir(self) -> Path:
        k = self.queue.key
        return POOL_DIR / (f"d{self.days}" if k == "rift" else f"{k}-d{self.days}")

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
        sql = BASE.format(days=self.days, qf=self.queue.filter()) + f"select pos, {AGG} from t group by puuid, pos"
        cols: dict[str, list[list[float]]] = {}
        keys = [m[1] for m in METRICS]
        for r in psql(sql):
            c = cols.setdefault(r[0], [[] for _ in keys])
            for i, v in enumerate(r[1:]):
                c[i].append(float(v))
        name = self._dir().name
        tmp = POOL_DIR / f".{name}.tmp{int(time.time())}"
        tmp.mkdir(parents=True, exist_ok=True)

        # 乱斗系按队列分开建池：450=普通大乱斗(ARAM)，2400=海克斯/乱斗狂欢(KIWI)，两者数据不能混。
        # 只有默认的 rift 池顺带建，别的峡谷队列池不重复建
        for qid, tag in (ARAM_QUEUES.items() if self.queue.key == "rift" else ()):
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
        (tmp / "meta.json").write_text(json.dumps({"ts": time.time(), "days": self.days, "queue": self.queue.key,
                                                   "counts": {p: len(v[0]) for p, v in cols.items()}}))
        # 原子换目录：别的进程正在读旧目录也不会读到半份
        dst = self._dir()
        old = POOL_DIR / f".{name}.old{int(time.time())}"
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


def pool(days: int, refresh: bool = False, queue: Queue | None = None) -> Pool:
    return Pool(days, refresh, queue)


def pool_key(q: Queue, pos: str) -> str:
    """比较池里的键：有分路的队列按位置，乱斗按队列 tag。"""
    return pos if q.laned else q.tag


def keys_of(q: Queue) -> list[str]:
    return [m[1] for m in METRICS] if q.laned else ARAM_KEYS


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
               champion: str | None = None, puuid: str | None = None, aram: bool | str = False,
               q: Queue | None = None) -> dict:
    """只扫这个玩家自己的对局，再补队伍合计（比全表窗口快一个数量级）。
    q 决定取哪个队列的对局；aram（旧参数，ARAM / KIWI）等价于传对应的乱斗队列。"""
    puuid = puuid or resolve(name)
    q = q or (aram and _scope_queue(aram)) or QUEUES["rift"]
    aram = q.tag if not q.laned else False
    extra = ",\n      coalesce(sum(mine.dmg::numeric) / nullif(sum(mine.tp) / 60.0, 0), 0)" if aram else ""
    scope = _scope(q, position, champion)
    pos_filter = ""
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
        what = q.name + ("分路局" if q.key == "rift" else "")
        if position:
            what += f" · 位置 {position}"
        if champion:
            what += f" · 英雄 {champion}"
        raise NoGames(f"{name} 最近 {days} 天没有对局（{what}）")
    row = {"name": name, "puuid": puuid, "pos": r[0][0], "games": int(r[0][1])}
    row.update({k: float(v) for k, v in zip([m[1] for m in METRICS], r[0][2:])})
    if aram and len(r[0]) > 10:
        row["dmg_per_min"] = float(r[0][10])
    return row


class NoGames(SystemExit):
    pass


def main_position(puuid: str, days: int, q: Queue | None = None, champion: str | None = None) -> str | None:
    """这个人在这个队列里打得最多的位置（给了英雄就只看这个英雄）。乱斗队列没有位置，返回 None。"""
    q = q or QUEUES["rift"]
    if not q.laned:
        return None
    r = psql(f"""select mp.team_position from lol.match_participant mp
                 join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
                   {_scope(q, None, champion)}
                 group by 1 order by count(*) desc limit 1""")
    return r[0][0] if r else None


def _scope(q: Queue | str | None, position: str | None = None, champion: str | None = None) -> str:
    """对局范围，所有取个人数据的 SQL 共用这一个口径：
    有分路的队列 = 分路局（超过 5 分钟）+ 队列过滤，可再限位置；乱斗 = 这个队列（超过 3 分钟）；都可再限英雄。
    q 也接受旧的乱斗标签（ARAM / KIWI）或 None（= rift），兼容老调用。"""
    if q is None:
        q = QUEUES["rift"]
    elif isinstance(q, str):
        q = next(x for x in QUEUES.values() if x.tag == q)
    if q.laned:
        s = "and mp.time_played > 300 and mp.team_position in ('TOP','JUNGLE','MIDDLE','BOTTOM','UTILITY')" + q.filter()
        if position:
            s += f" and mp.team_position = {sql_str(position)}"
    else:
        s = "and mp.time_played > 180" + q.filter()
    if champion:
        s += f" and mp.champion_name = {sql_str(champion)}"
    return s


def extras(puuid: str, days: int, position: str | None, champion: str | None = None,
           aram: str | None = None, q: Queue | None = None) -> dict:
    """对位卡用的附加信息。profile_icon 列可能还没建（下一轮入库才会 alter），用 to_jsonb 兼容。
    近 10 局和常用英雄只算队列 q（aram 是旧参数，传 ARAM / KIWI 等价于对应的乱斗队列）。"""
    if not puuid or not RE_PUUID.match(puuid):
        return {}
    q = q or (aram and _scope_queue(aram)) or QUEUES["rift"]
    pu = sql_str(puuid)
    out: dict = {}
    r = psql(f"select to_jsonb(p)->>'profile_icon' from lol.player p where p.puuid = {pu}")
    out["profile_icon"] = int(r[0][0]) if r and r[0][0] else None
    r = psql(f"select tier, division, lp from lol.player_rank where puuid = {pu} order by updated_at desc limit 1")
    if r:
        out["rank"] = {"tier": r[0][0], "division": r[0][1], "lp": int(r[0][2] or 0)}
    r = psql(f"""select mp.champion_name, mp.win from lol.match_participant mp
                 join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {pu} {_scope(q, position, champion)}
                 order by m.game_creation desc limit 10""")
    out["recent"] = [{"champion": x[0], "win": x[1] == "t"} for x in r]
    # 常用英雄：峡谷默认队列沿用原来的口径（只要求超过 5 分钟，不要求有分路），其余队列和取数同口径
    top_scope = _scope(q, position, champion) if q.key != "rift" else (
        "and mp.time_played > 300" + (f" and mp.team_position = {sql_str(position)}" if position else "")
        + (f" and mp.champion_name = {sql_str(champion)}" if champion else ""))
    r = psql(f"""select mp.champion_name, count(*), sum(case when mp.win then 1 else 0 end)
                 from lol.match_participant mp join lol.match m on m.match_id = mp.match_id
                 where mp.puuid = {pu} and m.game_creation > now() - interval '{int(days)} days'
                   {top_scope}
                 group by 1 order by 2 desc limit 4""")
    out["top_champs"] = [{"champion": x[0], "games": int(x[1]), "wins": int(x[2])} for x in r]
    return out


def _scope_queue(tag: str) -> Queue:
    return next(x for x in QUEUES.values() if x.tag == tag)


def champ_wins(puuid: str, champ: str, days: int, games: int, position: str | None,
               q: Queue | None = None) -> int:
    q = q or QUEUES["rift"]
    # 峡谷默认队列沿用原来的口径（只要求超过 5 分钟）；其余队列和取数同口径
    scope = (_scope(q, position, None) if q.key != "rift" else
             "and mp.time_played > 300" + (f" and mp.team_position = {sql_str(position)}" if position else ""))
    r = psql(f"""select count(*) filter (where win) from (
                   select mp.win from lol.match_participant mp join lol.match m on m.match_id = mp.match_id
                   where mp.puuid = {sql_str(puuid)} and mp.champion_name = {sql_str(champ)}
                     and m.game_creation > now() - interval '{int(days)} days' {scope}
                   order by m.game_creation desc limit {int(games)}) x""")
    return int(r[0][0]) if r else 0


def avg_kda_line(puuid: str, days: int, games: int, position: str | None, champion: str | None = None,
                 aram: str | None = None, q: Queue | None = None) -> str:
    q = q or (aram and _scope_queue(aram)) or QUEUES["rift"]
    r = psql(f"""select avg(k), avg(d), avg(a) from (
                   select mp.kills k, mp.deaths d, mp.assists a from lol.match_participant mp
                   join lol.match m on m.match_id = mp.match_id
                   where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
                     {_scope(q, position, champion)}
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
    sql = (BASE.format(days=int(days), qf="")
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
        r = list(r)
        r[16] = int(r[16]) if r[16] not in (None, "") else None   # team_id：100 蓝方，200 红方
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


BLUE, RED = 100, 200
SIDE_CN = {BLUE: "蓝方", RED: "红方"}


def blue_first(pair: list[dict]) -> list[dict]:
    """单局两个人：蓝方（team 100）放左边，红方（200）放右边，和图上蓝/红配色、客户端左右一致。
    同一队的两个人（跨位置比较）保持原来的顺序。"""
    if len(pair) == 2 and pair[0].get("team") == RED and pair[1].get("team") == BLUE:
        return [pair[1], pair[0]]
    return pair


def match_teams(match_id: str) -> dict:
    """整局的比分条数据：每队的人头、经济、胜负和资源（塔 / 小龙 / 先锋 / 虚空巢虫 / 大龙 / 水晶）。
    资源来自 lol.match_team.objectives；没有这一行（老数据或乱斗）就只给人头、经济、胜负。"""
    mid = sql_str(match_id)
    t: dict[int, dict] = {}
    for tid, k, d, a, gold, win in psql(f"""
            select team_id, sum(kills), sum(deaths), sum(assists), sum(gold_earned), bool_or(win)
            from lol.match_participant where match_id = {mid} group by 1"""):
        t[int(tid)] = {"kills": int(k or 0), "deaths": int(d or 0), "assists": int(a or 0),
                       "gold": int(gold or 0), "win": win == "t"}
    for tid, obj in psql(f"select team_id, objectives::text from lol.match_team where match_id = {mid}"):
        if int(tid) in t and obj:
            try:
                o = json.loads(obj)
            except ValueError:
                continue
            t[int(tid)]["obj"] = {k: int((v or {}).get("kills") or 0) for k, v in o.items()}
            t[int(tid)]["first"] = {k for k, v in o.items() if (v or {}).get("first")}
    r = psql(f"select duration_sec from lol.match where match_id = {mid}")
    dur = int(r[0][0]) if r and r[0][0] else None
    return {"teams": t, "duration": dur}


def score_rows(rows: list[dict], curves: dict, aram_tag: str | None) -> list[dict]:
    """单局里每个人的八维数值和同位置分位（乱斗：和同模式全体比），以及溢出比例 fracs。
    单局对位和对局清算共用这一份算法。"""
    out = []
    for r in rows:
        gm = game_metrics(r)
        pos = aram_tag or r["pos"]
        if aram_tag:
            mins = max(r["tp"] / 60, 1)
            gm = {k: gm[k] for k in ARAM_KEYS if k in gm} | {"dmg_per_min": r["dmg"] / mins}
        curve = curves.get(pos, {})
        pc = {k: pct_from_curve(gm[k], curve.get(k, [])) for k in gm}
        fcs = {}
        for k in gm:
            cv, p = curve.get(k, []), pc[k]
            if len(cv) >= 99 and gm[k] > cv[98]:
                p90, p99 = cv[89], cv[98]
                span = (p99 - p90) or max(abs(p99) * 0.02, 1e-9)
                fcs[k] = 1.0 + min(0.45, 0.15 + (gm[k] - p99) / span * 0.20)
            else:
                fcs[k] = max(0.04, p / 95.0) if p <= 95 else 1.0
        out.append({"row": r, "pos": pos, "metrics": gm, "pcts": pc, "fracs": fcs})
    return out


def recap_pairs(scored: list[dict], laned: bool) -> list[tuple[dict | None, dict | None]]:
    """清算图的五行：峡谷按位置配（上 / 野 / 中 / 下 / 辅，缺位置的放最后），
    乱斗没有分路，两队各按本局评分从高到低，第 1 配第 1。"""
    from lol_card.scoring import score as _score
    blue = [s for s in scored if s["row"]["team"] == BLUE]
    red = [s for s in scored if s["row"]["team"] == RED]
    for s in scored:
        s["score"] = _score(s["pcts"], s["pos"])
    if laned:
        order = {p: i for i, p in enumerate(POSITIONS)}
        key = lambda s: order.get(s["pos"], 9)   # noqa: E731
    else:
        key = lambda s: -s["score"]   # noqa: E731
    blue.sort(key=key)
    red.sort(key=key)
    n = max(len(blue), len(red))
    return [(blue[i] if i < len(blue) else None, red[i] if i < len(red) else None) for i in range(n)][:5]


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
        st = player_row(name, a.days, a.games, pos, puuid=puuid, q=a.q)
    except NoGames:
        try:
            st = player_row(name, a.days, a.games, None, puuid=puuid, q=a.q)
            fallback = True
        except NoGames:
            pass
    ex = extras(puuid, a.days, None if fallback else pos, q=a.q)
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
        conv = lambda t: [{"puuid": r["puuid"], "name": r["name"], "pos": r["pos"], "champion": r["champ"]}
                          for r in rows if r["team"] == t]
        # 和单局一样：蓝方固定在左（蓝色）、红方在右。给了名字时只在名字所在那一方后面标「我方」，
        # 不再把那一方挪到左边——挪过去还是画成蓝色，红方的人就被画成了蓝方
        ally, enemy = conv(BLUE), conv(RED)
        mine = None
        if a.players:
            want = {n.split("#")[0] for n in a.players}
            mine = next((r["team"] for r in rows if r["name"].split("#")[0] in want), None)
        sides = tuple(SIDE_CN[t] + ("（我方）" if t == mine else "") for t in (BLUE, RED))
        sub_head = f"{a.match_id} 的十人 · 按各自近期数据（含本局）"

    def order(team):
        by_pos = {e.get("pos"): e for e in team if e.get("pos") in TEAM_ORDER}
        rest = [e for e in team if e.get("pos") not in TEAM_ORDER or by_pos.get(e.get("pos")) is not e]
        out = []
        for pos in TEAM_ORDER:
            out.append((pos, by_pos.get(pos) or (rest.pop(0) if rest else {})))
        return out

    pool_rows = pool(a.pool_days, refresh=a.refresh, queue=a.q)
    pool_rows.count("TOP")  # 先在主线程把池子备好，免得 10 个线程同时冷启动重建
    jobs = order(ally) + order(enemy)
    res = pmap(lambda pe: team_player(pe[1], pe[0], a, pool_rows, champ_names), jobs, workers=10)
    left, right = res[:5], res[5:]
    hidden = sum(1 for p in right if p.get("missing_player"))
    render_team(left, right, Path(a.out), {
        "title": ("" if a.q.key == "rift" else f"{a.q.name} · ") + "战前战力 · 5v5 对位",
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


def player_pos_rows(puuid: str, days: int, games: int, q: Queue | None = None) -> list[dict]:
    """这个人最近 N 局（不限位置），按位置拆开各算一组八维均值。SQL 与 player_row 同口径。"""
    sql = f"""
    with mine as (
      select mp.match_id, mp.team_id, mp.team_position, mp.damage_to_champ dmg, mp.gold_earned gold,
             mp.cs, mp.vision_score vis, mp.kills k, mp.deaths d, mp.assists a, mp.time_played tp,
             mp.damage_taken taken, m.game_creation
      from lol.match_participant mp
      join lol.match m on m.match_id = mp.match_id
      where mp.puuid = {sql_str(puuid)} and m.game_creation > now() - interval '{int(days)} days'
        {_scope(q, None, None)}
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
    q = a.q
    pool_rows = pool(a.pool_days, refresh=a.refresh, queue=q)
    pool_rows.count("TOP")  # 主线程先备好池子，别让两个线程同时冷启动
    puuids = pmap(resolve, a.players)

    def build(np_):
        name, pu = np_
        rows = player_pos_rows(pu, a.days, a.games, q)
        if not rows:
            raise NoGames(f"{name} 最近 {a.days} 天没有{q.name}分路对局")
        tot = sum(x["games"] for x in rows)
        per = []
        for x in rows:
            pc = pool_rows.pcts(x["pos"], x)
            per.append({"pos": x["pos"], "games": x["games"], "pcts": pc, "score": score(pc, x["pos"])})
        pcts = {k: sum(p["pcts"][k] * p["games"] for p in per) / tot for k in keys}
        stats = {k: sum(x[k] * x["games"] for x in rows) / tot for k in keys}
        sc = round(sum(p["score"] * p["games"] for p in per) / tot, 1)
        ex = extras(pu, a.days, None, q=q)
        return {
            **ex, "name": name, "puuid": pu, "pos": "ALL", "games": tot,
            "stats": stats, "pcts": pcts, "score": sc, "pos_breakdown": per,
            "pool_note": f"最近 {tot} 局 · {len(per)} 个位置", "low_sample": tot < a.min_games,
            "pool_size": min(pool_rows.count(p["pos"]) for p in per),
            "champion": (ex.get("top_champs") or [{}])[0].get("champion"),
            "kda_line": avg_kda_line(pu, a.days, a.games, None, q=q),
        }

    cards = pmap(build, list(zip(a.players, puuids)))
    qtag = "" if q.key == "rift" else f"{q.name} · "
    render(cards, Path(a.out), {
        "small_pool": min(c["pool_size"] for c in cards) < SMALL_POOL,
        "title": qtag + ("整体实力 · 全位置" if len(cards) == 2 else "整体实力 · 全位置雷达"),
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
    ap.add_argument("--mode", choices=["duel", "match", "champ", "team", "overall", "aram", "recap"], default=None,
                    help="图怎么排：duel=1–2 人近期对比；match=单局；champ=只比同一个英雄；team=5v5 全队；"
                         "overall=整体实力；recap=对局清算（一局十人本局能力，峡谷/乱斗都行，要 --match-id）。"
                         "和 --queue 组合，例如 duel + kiwi = 海克斯乱斗对比。aram = duel + --queue aram 的简写")
    ap.add_argument("--champion", default=None, help="champ 模式：英雄（英文 id / 中文名 / 称号）")
    ap.add_argument("--live", action="store_true", help="team 模式：从 PC 客户端读当前对局/选人")
    ap.add_argument("--pool-days", type=int, default=None,
                    help="同位置池子的时间窗（默认 = min(--days, 30)）。--days 只管取个人对局")
    ap.add_argument("--min-games", type=int, default=5, help="少于这么多局在图上标红提示样本不足")
    ap.add_argument("--queue", choices=list(QUEUES), default=None,
                    help="和谁比（取哪个队列的对局、比较池用哪个队列的人），可以和任何模式组合："
                         "rift=峡谷全部分路局（默认）；solo=单双排；flex=灵活排位；ranked=单双排+灵活；"
                         "normal=匹配；aram=普通大乱斗；kiwi=海克斯乱斗")
    ap.add_argument("--aram-queue", choices=["aram", "kiwi"], default=None,
                    help="旧参数，等价于 --mode aram --queue aram|kiwi")
    a = ap.parse_args()
    a.mode = a.mode or ("match" if a.match_id else "duel")
    # aram 模式 = duel + 乱斗队列（旧写法照样能用）
    if a.mode == "aram":
        a.mode = "duel"
        a.queue = a.queue or a.aram_queue or "aram"
        if QUEUES[a.queue].laned:
            ap.error(f"aram 模式只能配乱斗队列（aram / kiwi），不能配 {a.queue}")
    elif a.aram_queue and not a.queue:
        a.queue = a.aram_queue
    a.q = QUEUES[a.queue or "rift"]
    if not a.q.laned and a.mode in ("overall", "team"):
        ap.error(f"{a.mode} 模式按位置算，{a.q.name}没有分路，不能组合")
    a.games = a.games or (50 if a.mode == "overall" else 20)
    a.pool_days = a.pool_days or min(a.days, 30)
    if not (1 <= a.pool_days <= 365):
        ap.error("--pool-days 取 1–365")
    if a.mode == "team":
        return team_mode(a)
    if a.mode == "recap":
        # 队列按那一局自己的（峡谷 / 乱斗自动识别），--queue 不用给
        if not (1 <= a.days <= 365):
            ap.error("--days 取 1–365")
        return recap_mode(a)
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
        # 左右按阵营定：蓝方在左（蓝色），红方在右（红色）。原来按「给的名字在左」，
        # 名字在红方时左边就成了红方的人却画成蓝色，看图分不清谁是哪边
        pair = blue_first(pair)
        board = match_teams(a.match_id)
        scored = score_rows(pair, curves, aram_tag)
        stats = [{"name": f"{s['row']['name']} · {s['row']['champ']}", "pos": s["row"]["pos"], "games": 1,
                  **s["metrics"]} for s in scored]
        pcts = [s["pcts"] for s in scored]
        fracs = [s["fracs"] for s in scored]
        notes = [f"同位置样本 {counts.get(s['row']['pos'], 0)} 局" for s in scored]
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
                "team": r.get("team"),
            })
        render(cards, Path(a.out), {
            "title": "单局对位 · 同位置百分位",
            "subtitle": f"{a.match_id} · 对局时长 {pair[0]['tp'] / 60:.0f} 分钟 · 分位池：最近 {a.days} 天同位置单局",
            "badge": POS_BADGE.get(pos, pos),
            "footer": "外圈 = 同位置 P95（满格） · 超过 P99 才溢出 · 评分 = 八维分位按位置加权（0–10）",
            "scoreboard": board,
            "template": "match",
        })
        T = board["teams"]
        winner = next((tid for tid, x in T.items() if x["win"]), None)
        print(json.dumps({
            "out": a.out, "match": a.match_id, "pos": pos,
            # 给机器人看的结论：哪方赢、比分，每个人在哪方、赢没赢——别再说反
            "result": {"winner": SIDE_CN.get(winner, "?"),
                       "kills": {SIDE_CN[k]: v["kills"] for k, v in T.items() if k in SIDE_CN},
                       "duration_min": round(board["duration"] / 60) if board["duration"] else None},
            "players": [{"name": r["name"], "champion": r["champ"], "side": SIDE_CN.get(r.get("team"), "?"),
                         "win": r["win"] == "t", "kda": f"{r['k']:.0f}/{r['d']:.0f}/{r['a']:.0f}",
                         "pct": {k: round(v, 1) for k, v in pc.items()}}
                        for r, pc in zip(pair, pcts)]},
            ensure_ascii=False))
        return 0

    champ_key = champ_cn = None
    if a.mode == "champ":
        from lol_radar_card import Assets
        hit = Assets().resolve_champion(a.champion)
        if not hit:
            raise SystemExit(f"不认识的英雄 {a.champion!r}")
        _, champ_key, champ_cn = hit

    return compare_mode(a, a.q, position, champ_key, champ_cn)


def recap_mode(a) -> int:
    """对局清算：一局十个人的本局能力，五行对位（峡谷按分路，乱斗按评分配对），左蓝右红。
    评分和单局对位是同一套：本局八维在同位置（乱斗：同模式全体）里的分位，按位置加权成 0–10。"""
    if not a.match_id or not RE_MATCH_ID.match(a.match_id):
        raise SystemExit("recap 模式需要合法的 --match-id（形如 HN1_1234567890）")
    rows = match_rows(a.match_id)
    if not rows:
        raise SystemExit(f"找不到对局 {a.match_id}")
    queue = rows[0].get("queue")
    aram_tag = ARAM_QUEUES.get(queue)
    laned = aram_tag is None
    if laned and all(r["pos"] not in POSITIONS for r in rows):
        raise SystemExit(f"这一局（queue {queue}）没有分路数据，既不是峡谷也不是大乱斗/海克斯乱斗，暂不支持")
    curves, _ = quantiles(a.days, refresh=a.refresh)
    pairs = recap_pairs(score_rows(rows, curves, aram_tag), laned)
    board = match_teams(a.match_id)
    # 十个人的当前段位，一次查完（每人取最新一条）。段位是「现在」的，不是那一局时的
    pus = [r["puuid"] for r in rows if r.get("puuid")]
    ranks = {}
    if pus:
        for pu, tier, div, lp in psql(
                "select distinct on (puuid) puuid, tier, division, lp from lol.player_rank "
                f"where puuid in ({', '.join(sql_str(p) for p in pus)}) order by puuid, updated_at desc"):
            ranks[pu] = {"tier": tier, "division": div, "lp": int(lp or 0)}

    def card(s):
        if s is None:
            return None
        r = s["row"]
        return {"name": r["name"], "champion": r["champ"], "pos": s["pos"], "lane_pos": r["pos"],
                "team": r["team"], "win": r["win"] == "t", "score": s["score"], "pcts": s["pcts"],
                "kda_line": f"{r['k']:.0f} / {r['d']:.0f} / {r['a']:.0f}",
                "rank": ranks.get(r.get("puuid"))}

    lanes = [(card(b), card(r_)) for b, r_ in pairs]
    from lol_card import render_recap
    render_recap(lanes, Path(a.out), {
        "title": "对局清算 · 五路对位" if laned else "对局清算 · 按评分配对",
        "scoreboard": board, "laned": laned,
        "queue_name": "峡谷" if laned else ("海克斯乱斗" if aram_tag == "KIWI" else "大乱斗"),
    })
    T = board["teams"]
    winner = next((tid for tid, x in T.items() if x["win"]), None)
    everyone = [c for pr in lanes for c in pr if c]
    mvp = max(everyone, key=lambda c: c["score"]) if everyone else None
    print(json.dumps({
        "out": a.out, "mode": "recap", "match": a.match_id, "laned": laned,
        "result": {"winner": SIDE_CN.get(winner, "?"),
                   "kills": {SIDE_CN[k]: v["kills"] for k, v in T.items() if k in SIDE_CN},
                   "duration_min": round(board["duration"] / 60) if board["duration"] else None},
        "mvp": {"name": mvp["name"], "champion": mvp["champion"], "side": SIDE_CN.get(mvp["team"], "?"),
                "score": mvp["score"]} if mvp else None,
        "lanes": [{"pos": (b or r_ or {}).get("lane_pos") if laned else f"第{i + 1}",
                   "blue": {"name": b["name"], "champion": b["champion"], "score": b["score"], "kda": b["kda_line"]} if b else None,
                   "red": {"name": r_["name"], "champion": r_["champion"], "score": r_["score"], "kda": r_["kda_line"]} if r_ else None}
                  for i, (b, r_) in enumerate(lanes)],
    }, ensure_ascii=False))
    return 0


def compare_mode(a, q: Queue, position: str | None, champ_key: str | None, champ_cn: str | None) -> int:
    """玩家对比：1–2 人，在队列 q 里取近期对局，和同队列（有分路的再按同位置）的人比。
    duel / champ / aram 都走这里，区别只是队列和是否限定英雄。"""
    if not q.laned and position:
        raise SystemExit(f"{q.name}没有分路，不能指定 --position")
    pool_rows = pool(a.pool_days, refresh=a.refresh, queue=q)
    keys = keys_of(q)
    puuids = pmap(resolve, a.players)
    if q.laned and len(a.players) == 2 and not position:
        # 两人对比默认锁同一位置：第一个人在这个队列里的主位置（champ 模式取他玩这个英雄最多的位置）
        position = main_position(puuids[0], a.days, q, champ_key)

    def row(np_):
        try:
            return player_row(np_[0], a.days, a.games, position, champion=champ_key, puuid=np_[1], q=q)
        except NoGames as e:
            if not q.laned or q.key != "rift":
                hint = "（海克斯乱斗用 --queue kiwi）" if q.key == "aram" else (
                    "（不限队列用 --queue rift）" if q.laned else "")
                raise NoGames(f"{e}{hint}")
            raise
    stats = pmap(row, list(zip(a.players, puuids)))
    pkeys = [pool_key(q, st["pos"]) for st in stats]
    pcts = [pool_rows.pcts(k, st, keys) for k, st in zip(pkeys, stats)]
    pool_n = [pool_rows.count(k) for k in pkeys]

    from lol_radar_card import render

    def side(st):
        pos = (position or st["pos"]) if q.laned else None
        ex = extras(st["puuid"], a.days, pos, champ_key, q=q)
        if champ_key:
            ex["top_champs"] = [{"champion": champ_key, "games": st["games"],
                                 "wins": champ_wins(st["puuid"], champ_key, a.days, a.games, pos, q)}]
        return ex, avg_kda_line(st["puuid"], a.days, a.games, pos, champ_key, q=q)
    sides = pmap(side, stats)
    cards = []
    for st, pk, pc, n, (ex, kda_line) in zip(stats, pkeys, pcts, pool_n, sides):
        low = st["games"] < a.min_games
        who = "同位置" if q.laned else f"{q.name}"
        cards.append({
            **ex, "name": st["name"], "pos": pk, "games": st["games"],
            "pool_note": f"最近 {st['games']} 局{'（样本少）' if low else ''} · {who}样本 {n} 人", "low_sample": low,
            "pool_size": n, "stats": {k: st[k] for k in pc}, "pcts": pc,
            "fracs": pool_rows.fracs(pk, st, keys),
            **({} if q.laned else {"axes": ARAM_AXES}),
            "champion": champ_key or (ex.get("top_champs") or [{}])[0].get("champion"),
            "kda_line": kda_line,
        })

    same = len({c["pos"] for c in cards}) == 1
    two = len(cards) == 2
    # 标题：峡谷默认队列沿用原来的写法；指定了队列就把队列名写进标题
    qtag = "" if q.key == "rift" else f"{q.name} · "
    if champ_key:
        title = f"{qtag}英雄专精 · {champ_cn}"
        sub = (f"最近 {a.days} 天 · 只算 {champ_cn} 的对局，每人最多 {a.games} 局"
               f" · 分位池：近 {a.pool_days} 天{'同位置所有英雄' if q.laned else q.name + '所有英雄'}")
    elif not q.laned:
        title = f"{q.name} · 同模式百分位"
        sub = f"最近 {a.days} 天 · 每人取最近 {a.games} 局 · 分位池：近 {a.pool_days} 天同模式"
    else:
        title = qtag + ("战前对位 · 同位置百分位" if two else "能力雷达 · 同位置百分位")
        sub = f"最近 {a.days} 天 · 每人取最近 {a.games} 局" + (
            f" · 统一按{POS_CN_SHORT.get(cards[0]['pos'])}比" if two and same else "")
        if a.pool_days != a.days:
            sub += f" · 分位池：近 {a.pool_days} 天"
    if not same:
        sub += " · 位置不同，跨位置比较仅供参考"
    pos0 = cards[0]["pos"]
    if not q.laned:
        badge = f"{q.name}对比" if two else q.name
    elif not same:
        badge = "跨位置"
    else:
        badge = POS_BADGE.get(pos0, pos0) if two else POS_CN_SHORT.get(pos0, pos0)
    render(cards, Path(a.out), {
        "title": title,
        "subtitle": sub,
        "badge": badge,
        "small_pool": min(pool_n) < SMALL_POOL,
        "footer": ("外圈 = 同模式 P95（满格） · 超过 P99 才溢出" if not q.laned else
                   "外圈 = 同位置 P95（满格） · 超过 P99 才溢出 · 评分 = 八维分位按位置加权（0–10）"),
    })
    out = {"out": a.out, "players": [
        {"name": s["name"], "pos": s["pos"], "games": s["games"],
         "pct": {k: round(v, 1) for k, v in pc.items()}} for s, pc in zip(stats, pcts)]}
    if q.key != "rift":
        out["queue"] = q.key
        for p, n in zip(out["players"], pool_n):
            p["pool"] = n
    if not q.laned:   # 和原来 aram 模式的输出字段对齐
        out = {"out": a.out, "mode": "aram", "players": [
            {"name": s["name"], "queue": q.key, "games": s["games"], "pool": n,
             "pct": {k: round(v, 1) for k, v in pc.items()}} for s, pc, n in zip(stats, pcts, pool_n)]}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
