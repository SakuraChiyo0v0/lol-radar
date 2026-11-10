#!/usr/bin/env python3
"""在 PC 上跑：读客户端当前对局 / 选人阶段的 10 个人，打印 JSON。

由 lol_radar.py --mode team --live 通过 ssh 调用（scp 到 PC 后执行），NAS 侧不直接碰 LCU。
只做只读 GET。选人阶段看不到对面的人（客户端只给 puuid 为空的占位），这时 enemy 里只有位置。

输出：
  {"phase": "...", "queue_id": 420, "source": "gameflow|champ-select",
   "ally": [{"puuid", "name", "pos", "champion_id"}...], "enemy": [...]}
"""
from __future__ import annotations

import base64
import json
import re
import ssl
import subprocess
import sys
import urllib.request

CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

POS_MAP = {"top": "TOP", "jungle": "JUNGLE", "middle": "MIDDLE", "mid": "MIDDLE",
           "bottom": "BOTTOM", "bot": "BOTTOM", "utility": "UTILITY", "support": "UTILITY"}


def creds():
    cmd = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "(Get-CimInstance Win32_Process -Filter \"Name='LeagueClientUx.exe'\").CommandLine"],
        capture_output=True, text=True, timeout=60).stdout
    port = re.search(r"--app-port=(\d+)", cmd or "")
    tok = re.search(r"--remoting-auth-token=([\w\-]+)", cmd or "")
    if not port or not tok:
        print(json.dumps({"error": "客户端没开"}, ensure_ascii=False))
        sys.exit(0)
    return port.group(1), tok.group(1)


PORT, TOKEN = creds()
AUTH = "Basic " + base64.b64encode(f"riot:{TOKEN}".encode()).decode()


def get(path):
    req = urllib.request.Request(f"https://127.0.0.1:{PORT}{path}", headers={"Authorization": AUTH})
    try:
        with urllib.request.urlopen(req, timeout=10, context=CTX) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception:
        return None


_names: dict[str, str] = {}


def name_of(puuid: str) -> str:
    if not puuid:
        return ""
    if puuid not in _names:
        s = get(f"/lol-summoner/v2/summoners/puuid/{puuid}") or {}
        gn, tl = s.get("gameName"), s.get("tagLine")
        _names[puuid] = f"{gn}#{tl}" if gn else ""
    return _names[puuid]


def from_gameflow(sess: dict):
    gd = sess.get("gameData") or {}
    me = (get("/lol-summoner/v1/current-summoner") or {}).get("puuid")
    t1, t2 = gd.get("teamOne") or [], gd.get("teamTwo") or []
    if not t1 and not t2:
        return None
    if me and any(p.get("puuid") == me for p in t2):
        t1, t2 = t2, t1

    def conv(team):
        out = []
        for p in team:
            pu = p.get("puuid") or ""
            out.append({"puuid": pu, "name": name_of(pu) or p.get("summonerName") or "",
                        "pos": POS_MAP.get((p.get("selectedPosition") or "").lower()),
                        "champion_id": p.get("championId") or None})
        return out
    return conv(t1), conv(t2), gd.get("queue", {}).get("id")


def from_champselect(cs: dict):
    def conv(team, hidden):
        out = []
        for p in team:
            pu = p.get("puuid") or ""
            out.append({"puuid": "" if hidden else pu, "name": "" if hidden else name_of(pu),
                        "pos": POS_MAP.get((p.get("assignedPosition") or "").lower()),
                        "champion_id": p.get("championId") or p.get("championPickIntent") or None})
        return out
    return conv(cs.get("myTeam") or [], False), conv(cs.get("theirTeam") or [], True)


phase = get("/lol-gameflow/v1/gameflow-phase")
res = {"phase": phase}
if phase in ("InProgress", "GameStart", "Reconnect", "WaitingForStats", "PreEndOfGame", "EndOfGame"):
    r = from_gameflow(get("/lol-gameflow/v1/session") or {})
    if r:
        res.update({"source": "gameflow", "ally": r[0], "enemy": r[1], "queue_id": r[2]})
elif phase == "ChampSelect":
    cs = get("/lol-champ-select/v1/session") or {}
    a, e = from_champselect(cs)
    res.update({"source": "champ-select", "ally": a, "enemy": e})
print(json.dumps(res, ensure_ascii=False))
