"""逐像素对比：同一份录制输入，用「基准」渲染代码和当前代码各画一张。

用法：python3 tools/compare.py <基准 lol_radar_card.py 或含 lol_card 包的目录>
例：  git show HEAD~1:lol_radar_card.py > /tmp/base_card.py && python3 tools/compare.py /tmp/base_card.py
      git worktree add /tmp/base HEAD~1 && python3 tools/compare.py /tmp/base

读 $LOL_RADAR_FIXTURES（默认 ./fixtures）下 tools/capture.py 录的 JSON，
输出每个模式的差异像素数和最大通道差；全 0 才说明改动没有改变画面。
图存在 fixtures/old_*.png（基准）和 fixtures/new_*.png（当前）。
"""
import importlib.util
import json
import os
import sys
import time
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
FIX = Path(os.environ.get("LOL_RADAR_FIXTURES") or ROOT / "fixtures")


def load_base(path: Path):
    """基准可以是单文件（重构前的 lol_radar_card.py），也可以是一个仓库目录（用它的 lol_card 包）。"""
    if path.is_dir():
        # 目录模式：在独立模块名下加载，避免和当前的 lol_card 冲突
        spec = importlib.util.spec_from_file_location("base_lol_card", path / "lol_card" / "__init__.py",
                                                      submodule_search_locations=[str(path / "lol_card")])
        m = importlib.util.module_from_spec(spec)
        sys.modules["base_lol_card"] = m
        spec.loader.exec_module(m)
        return m
    spec = importlib.util.spec_from_file_location("base_card", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    base = load_base(Path(sys.argv[1]).resolve())
    sys.path.insert(0, str(ROOT))
    import lol_card as cur

    A_old, A_new = base.Assets(), cur.Assets()
    bad = 0
    fixtures = sorted(FIX.glob("*.json"))
    if not fixtures:
        print(f"{FIX} 下没有录制数据，先跑 tools/capture.py")
        return 2
    for fx in fixtures:
        j = json.loads(fx.read_text(encoding="utf-8"))
        o, n = FIX / f"old_{fx.stem}.png", FIX / f"new_{fx.stem}.png"
        t0 = time.time()
        if j["kind"] == "team":
            base.render_team(j["left"], j["right"], o, j["meta"], A_old)
            t1 = time.time()
            cur.render_team(j["left"], j["right"], n, j["meta"], A_new)
        else:
            base.render(j["players"], o, j["meta"], A_old)
            t1 = time.time()
            cur.render(j["players"], n, j["meta"], A_new)
        t2 = time.time()
        diff = ImageChops.difference(Image.open(o).convert("RGB"), Image.open(n).convert("RGB"))
        box = diff.getbbox()
        # 有差异的像素数：三通道差相加后转灰度，非零即有差异
        mask = diff.convert("L").point(lambda v: 255 if v else 0)
        px = mask.histogram()[255] if box else 0
        mx = max(max(c) for c in diff.getextrema())
        bad += px > 0
        print(f"{fx.stem:8s} diff_px={px:7d} max={mx:3d}  base={t1 - t0:.2f}s cur={t2 - t1:.2f}s"
              + (f"  bbox={box}" if px else ""))
    print("ALL_IDENTICAL" if not bad else f"{bad} DIFFER")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
