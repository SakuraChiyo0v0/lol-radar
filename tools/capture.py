"""录制各模式传给渲染层的输入（cards + meta），作为改动前后逐像素对比的基准。

用法：python3 tools/capture.py <名字> <lol_radar.py 的参数...>
例：  python3 tools/capture.py match --mode match --match-id HN1_123 --players "Player#1234"

把 lol_radar_card.render / render_team 换成「先把入参存成 JSON 再照常画」，跑一次 lol_radar.main()。
录好的数据放在 $LOL_RADAR_FIXTURES（默认 ./fixtures），之后对比只读这些 JSON，不再查库。
注意：录下来的是真实玩家数据，不要提交进仓库（.gitignore 已排除 fixtures/）。
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lol_radar_card as C  # noqa: E402

FIX = Path(os.environ.get("LOL_RADAR_FIXTURES") or ROOT / "fixtures")
FIX.mkdir(parents=True, exist_ok=True)
name = sys.argv[1]
_render, _render_team = C.render, C.render_team


def render(players, out, meta, assets=None):
    (FIX / f"{name}.json").write_text(json.dumps({"kind": "render", "players": players, "meta": meta},
                                                 ensure_ascii=False, default=str), encoding="utf-8")
    return _render(players, out, meta, assets)


def render_team(left, right, out, meta, assets=None):
    (FIX / f"{name}.json").write_text(json.dumps({"kind": "team", "left": left, "right": right, "meta": meta},
                                                 ensure_ascii=False, default=str), encoding="utf-8")
    return _render_team(left, right, out, meta, assets)


C.render, C.render_team = render, render_team
import lol_radar  # noqa: E402

sys.argv = ["lol_radar.py", *sys.argv[2:], "--out", str(FIX / f"live_{name}.png")]
rc = lol_radar.main()
print(name, "rc", rc, "fixture", (FIX / f"{name}.json").exists())
