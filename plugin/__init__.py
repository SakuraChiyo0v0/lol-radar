"""``lol_radar`` 工具：英雄联盟能力雷达 / 对位卡，一次调用出一张图。

为什么做成插件工具而不是让模型拼命令：
- 参数是结构化的（模式、玩家、位置、天数），模型不用记 CLI，也就不会拼错引号、把中文名截断；
- 这里统一做输入校验（玩家名格式、位置别名、数字范围），用 argv 列表起子进程，不经 shell，
  玩家名里有空格/引号/中文都不会出事，也没有注入面；
- 出图路径按调用唯一生成，并发调用不会互相覆盖；返回 MEDIA: 行，平台适配器负责发图。

底层还是 lol_radar.py，口径、缓存、性能都和命令行完全一致。

环境变量（都有默认值）：
  LOL_RADAR_SCRIPT       lol_radar.py 的位置，默认 $LOL_RADAR_HOME/scripts/lol_radar.py
  LOL_RADAR_TOOL_OUT_DIR 工具出图目录，默认 $LOL_RADAR_HOME/cache/charts/tool
  LOL_RADAR_PYTHON       跑脚本用的解释器，默认 python3
  LOL_RADAR_TOOL_TIMEOUT 单次出图超时秒数，默认 240
  LOL_RADAR_TOOL_KEEP    出过的图保留秒数，默认 3 天
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
import uuid
from pathlib import Path


def _env(name: str, default: str) -> str:
    return (os.environ.get(name) or "").strip() or default


_HOME = _env("LOL_RADAR_HOME", "/opt/data")
SCRIPT = _env("LOL_RADAR_SCRIPT", f"{_HOME}/scripts/lol_radar.py")
OUT_DIR = Path(_env("LOL_RADAR_TOOL_OUT_DIR", f"{_HOME}/cache/charts/tool"))
PYTHON = _env("LOL_RADAR_PYTHON", "python3")
KEEP_SECONDS = int(_env("LOL_RADAR_TOOL_KEEP", str(3 * 86400)))   # 出过的图保留多久，之后下次调用顺手清掉
TIMEOUT = int(_env("LOL_RADAR_TOOL_TIMEOUT", "240"))              # 冷启动建 30 天池子约 75 秒，留足余量

MODES = ("duel", "overall", "champ", "match", "team")


def _allow_live() -> bool:
    """live 会 ssh 到用户 PC 读客户端当前对局。按插件所在 profile 决定：
    <profile home>/lol-radar.json 里 {"allow_live": false} 就关。文件不存在 = 允许（default 自用）。
    多路复用网关里 HERMES_HOME 恒为 default，所以用插件自身位置反推 home（同 social-cli）。
    """
    cfg = Path(__file__).resolve().parents[2] / "lol-radar.json"
    try:
        return bool(json.loads(cfg.read_text(encoding="utf-8")).get("allow_live", True))
    except (OSError, ValueError):
        return True
RE_PLAYER = re.compile(r"^[^#\x00-\x1f]{1,32}#[0-9A-Za-z]{2,8}$")
RE_MATCH = re.compile(r"^[A-Z0-9]+_\d+$")
POS_OK = {
    "TOP", "JUNGLE", "JG", "MIDDLE", "MID", "BOTTOM", "BOT", "ADC", "UTILITY", "SUP", "SUPPORT",
    "上单", "打野", "中单", "下路", "辅助",
}


def _err(msg: str) -> str:
    return json.dumps({"ok": False, "error": msg}, ensure_ascii=False)


def _clean_old() -> None:
    try:
        now = time.time()
        for p in OUT_DIR.glob("radar_*.png"):
            if now - p.stat().st_mtime > KEEP_SECONDS:
                p.unlink(missing_ok=True)
    except Exception:
        pass


def _int(params: dict, key: str, lo: int, hi: int) -> int | None:
    v = params.get(key)
    if v in (None, ""):
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise ValueError(f"{key} 要是整数")
    if not lo <= n <= hi:
        raise ValueError(f"{key} 取 {lo}–{hi}")
    return n


def build_argv(params: dict, out: str) -> list[str]:
    """把工具参数翻成 lol_radar.py 的 argv。校验不过抛 ValueError（文案直接给模型看）。"""
    p = params or {}
    mode = str(p.get("mode") or "").strip().lower() or None
    players = p.get("players") or []
    if isinstance(players, str):
        players = [x for x in re.split(r"[,，\s]+", players) if x]
    players = [str(x).strip() for x in players if str(x).strip()]
    for name in players:
        if not RE_PLAYER.match(name):
            raise ValueError(f"玩家要写成「名字#tag」，例如 Player#1234：{name!r} 不合格")
    if len(players) > 2:
        raise ValueError("players 最多两个")
    match_id = str(p.get("match_id") or "").strip()
    if match_id and not RE_MATCH.match(match_id):
        raise ValueError(f"match_id 格式不对：{match_id!r}（应形如 HN1_1234567890）")
    live = bool(p.get("live"))
    champion = str(p.get("champion") or "").strip()
    position = str(p.get("position") or "").strip()
    if position and position.upper() not in POS_OK and position not in POS_OK:
        raise ValueError("position 可用：TOP / JUNGLE(JG) / MID / ADC(BOT) / SUP，或 上单/打野/中单/下路/辅助")
    if champion and (len(champion) > 32 or any(ord(c) < 32 for c in champion)):
        raise ValueError("champion 不合法")

    if mode is None:
        mode = "match" if match_id else ("team" if live else "duel")
    if mode not in MODES:
        raise ValueError(f"mode 可用：{' / '.join(MODES)}")
    if mode in ("duel", "overall") and not players:
        raise ValueError(f"{mode} 模式需要 players（1–2 人）")
    if mode == "champ" and not (champion and players):
        raise ValueError("champ 模式需要 champion 和 players")
    if mode == "match" and not match_id:
        raise ValueError("match 模式需要 match_id")
    if live and not _allow_live():
        raise ValueError("这里不开放读客户端实时对局（live）。可以给 match_id 用库里已有的对局出全队图")
    if mode == "team" and not (live or match_id):
        raise ValueError("team 模式需要 live=true（读 PC 客户端当前对局/选人）或 match_id")

    argv = [PYTHON, SCRIPT, "--mode", mode, "--out", out]
    if players:
        argv += ["--players", *players]
    if match_id:
        argv += ["--match-id", match_id]
    if live and mode == "team":
        argv += ["--live"]
    if champion:
        argv += ["--champion", champion]
    if position:
        argv += ["--position", position]
    for key, flag, lo, hi in (("days", "--days", 1, 365), ("games", "--games", 1, 500),
                              ("pool_days", "--pool-days", 1, 365), ("min_games", "--min-games", 1, 100)):
        n = _int(p, key, lo, hi)
        if n is not None:
            argv += [flag, str(n)]
    return argv


def lol_radar(params, **kwargs) -> str:
    del kwargs
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    _clean_old()
    out = str(OUT_DIR / f"radar_{time.strftime('%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png")
    try:
        argv = build_argv(params or {}, out)
    except ValueError as exc:
        return _err(str(exc))

    t0 = time.time()
    try:
        cp = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT,
                            cwd=os.path.dirname(SCRIPT), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return _err(f"出图超时（>{TIMEOUT}s）。多半是池子在冷启动，稍后再试一次通常就快了")
    took = round(time.time() - t0, 1)

    if cp.returncode != 0 or not os.path.isfile(out):
        msg = (cp.stderr or cp.stdout or "").strip().splitlines()
        return _err(msg[-1][:300] if msg else f"出图失败（退出码 {cp.returncode}）")

    summary = None
    for line in reversed((cp.stdout or "").strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                summary = json.loads(line)
            except json.JSONDecodeError:
                pass
            break
    return json.dumps({
        "ok": True,
        "mode": argv[argv.index("--mode") + 1],
        "seconds": took,
        "summary": summary,
        "reply_line": f"MEDIA:{out}",
        "instruction": ("把 reply_line 原样单独放进最终回复（不要改路径、不要放进代码块），平台会把它发成图片。"
                        "再用一两句话说清结论：谁评分高、最大差距在哪一维、样本少（标红）的要提醒只是参考。"),
    }, ensure_ascii=False)


SCHEMA = {
    "name": "lol_radar",
    "description": (
        "League of Legends (LoL) radar chart / matchup card image: player stats, rank percentile, "
        "compare players, duel, champion, match review, team 5v5 power. "
        "出英雄联盟能力雷达 / 对位卡图片（八维同位置百分位 + 0–10 综合评分 + 称号）。"
        "用户想看「某人/两个人的水平」「战前对位谁强」「这把打得怎么样」「我们队和对面谁强」时用。"
        "mode：duel=两人同位置近期对比（默认，单人就是个人雷达）；overall=整体实力，五个位置分别算再加权；"
        "champ=只比同一个英雄；match=某一局里同位置两人的本场发挥；team=5v5 全队战力。"
        "玩家一律写成「名字#tag」。成功后把 reply_line 原样放进最终回复。"
        "某人局数少时会标红，要提醒用户只是参考；局数太少可以把 days 拉长（如 365）多取几局。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": list(MODES),
                     "description": "不填时：给了 match_id 走 match，live=true 走 team，否则 duel"},
            "players": {"type": "array", "items": {"type": "string"}, "maxItems": 2,
                        "description": "1–2 个玩家，格式「名字#tag」，例如 [\"Player#1234\", \"Rival#5678\"]"},
            "position": {"type": "string",
                         "description": "位置：TOP/JG/MID/ADC/SUP 或 上单/打野/中单/下路/辅助。duel 不填时按第一个人的主位置"},
            "days": {"type": "integer", "description": "取个人对局的时间窗（天），默认 30，1–365"},
            "games": {"type": "integer", "description": "每人最多取多少局，默认 20（overall 默认 50），1–500"},
            "pool_days": {"type": "integer", "description": "同位置比较池的时间窗（天），默认 min(days, 30)。一般不用填"},
            "champion": {"type": "string", "description": "champ 模式用：英雄英文 id / 中文名 / 称号 / 常用简称（EZ、女警…）"},
            "match_id": {"type": "string", "description": "match 或 team 模式用：对局 ID，例如 HN1_1234567891"},
            "live": {"type": "boolean", "description": "team 模式：从用户 PC 客户端读当前选人/对局的 10 个人（需要 PC 开着且在选人或游戏中）"},
            "min_games": {"type": "integer", "description": "少于这么多局标红「样本少」，默认 5"},
        },
        "required": [],
    },
}


def register(ctx) -> None:
    ctx.register_tool(name="lol_radar", toolset="lol-radar", schema=SCHEMA, handler=lol_radar)
