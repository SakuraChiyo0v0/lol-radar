"""ddragon 图标 + 符文/召唤师技能/英雄表，全部落盘缓存到 <LOL_RADAR_DDRAGON_CACHE>/<ver>/。
拿不到图返回 None，画的时候出占位块，不让出图失败。"""
from __future__ import annotations

import json
import ssl
import time
import urllib.request

from PIL import Image

from .config import DDRAGON_CACHE as DD_CACHE
from .config import DDRAGON_FALLBACK_VERSION, SPLASH_CACHE

DD = "https://ddragon.leagueoflegends.com"

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE


def _get(url: str, timeout: int = 10) -> bytes | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout, context=_CTX) as r:
            return r.read()
    except Exception:
        return None


# 常见简称 → 英文 id（只收群里常说的，别的走中文名 / 称号匹配）
CHAMP_ALIAS = {
    "EZ": "Ezreal", "VN": "Vayne", "MF": "MissFortune", "TF": "TwistedFate", "JS": "JarvanIV",
    "LB": "Leblanc", "EZREAL": "Ezreal", "猴子": "MonkeyKing", "大嘴": "KogMaw", "小炮": "Tristana",
    "女警": "Caitlyn", "老鼠": "Twitch", "盲僧": "LeeSin", "瞎子": "LeeSin", "劫": "Zed",
    "卡莎": "Kaisa", "金克丝": "Jinx", "德莱文": "Draven", "赵信": "XinZhao", "皇子": "JarvanIV",
}


class Assets:
    def __init__(self) -> None:
        DD_CACHE.mkdir(parents=True, exist_ok=True)
        self.ver = self._version()
        self.dir = DD_CACHE / self.ver
        self.dir.mkdir(parents=True, exist_ok=True)
        self._runes: dict[int, str] | None = None
        self._spells: dict[int, str] | None = None
        self._champ_keys: dict[str, str] | None = None
        self._cd: dict | None = None
        self._local: dict[str, str] | None = None   # 素材库原画索引，第一次用时查

    # ---- 版本 / 数据表
    def _version(self) -> str:
        vf = DD_CACHE / "version.txt"
        if vf.exists() and time.time() - vf.stat().st_mtime < 86400:
            return vf.read_text().strip()
        raw = _get(f"{DD}/api/versions.json")
        if raw:
            ver = json.loads(raw)[0]
            vf.write_text(ver)
            return ver
        return vf.read_text().strip() if vf.exists() else DDRAGON_FALLBACK_VERSION

    def _json(self, name: str) -> dict | list | None:
        p = self.dir / name
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
        raw = _get(f"{DD}/cdn/{self.ver}/data/zh_CN/{name}")
        if not raw:
            return None
        p.write_bytes(raw)
        return json.loads(raw)

    def _img(self, rel: str, url: str) -> Image.Image | None:
        p = self.dir / rel
        if not p.exists():
            raw = _get(url)
            if not raw:
                return None
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(raw)
        try:
            return Image.open(p).convert("RGBA")
        except Exception:
            return None

    def runes(self) -> dict[int, str]:
        if self._runes is None:
            self._runes = {}
            for tree in self._json("runesReforged.json") or []:
                self._runes[tree["id"]] = tree["icon"]
                for slot in tree["slots"]:
                    for r in slot["runes"]:
                        self._runes[r["id"]] = r["icon"]
        return self._runes

    def spells(self) -> dict[int, str]:
        if self._spells is None:
            data = (self._json("summoner.json") or {}).get("data", {})
            self._spells = {int(v["key"]): v["image"]["full"] for v in data.values()}
        return self._spells

    def _champ_data(self) -> dict:
        if self._cd is None:
            self._cd = (self._json("champion.json") or {}).get("data", {})
        return self._cd

    # ---- 英雄名字解析
    def champ_key(self, name: str) -> str:
        """库里 champion_name 是英文 id（如 TwistedFate），个别和 ddragon 不一致时按 key 兜底。"""
        if self._champ_keys is None:
            data = self._champ_data()
            self._champ_keys = {k.lower(): k for k in data}
            self._champ_names = {k: v["name"] for k, v in data.items()}
        return self._champ_keys.get((name or "").lower(), name or "")

    def champ_cn(self, name: str) -> str:
        key = self.champ_key(name)
        return getattr(self, "_champ_names", {}).get(key, name)

    def champ_key_by_id(self, cid) -> str | None:
        for k, v in self._champ_data().items():
            if str(v.get("key")) == str(cid):
                return k
        return None

    def champ_title(self, key: str) -> str:
        v = self._champ_data().get(self.champ_key(key)) or {}
        return v.get("title") or v.get("name") or key

    def resolve_champion(self, text: str) -> tuple[int, str, str] | None:
        """英文 id / 数字 id / 中文称号（伊泽瑞尔）/ 中文名（探险家）都认；先精确后包含。"""
        t = (text or "").strip()
        t = CHAMP_ALIAS.get(t.upper(), CHAMP_ALIAS.get(t, t))
        tl = t.lower()
        data = self._champ_data()
        for k, v in data.items():
            if tl in (k.lower(), str(v.get("key"))) or t in (v.get("name"), v.get("title")):
                return int(v["key"]), k, v.get("title") or v.get("name")
        for k, v in data.items():
            if t and (t in (v.get("title") or "") or t in (v.get("name") or "")):
                return int(v["key"]), k, v.get("title") or v.get("name")
        return None

    # ---- 图标
    def profile(self, icon_id: int | None):
        if not icon_id:
            return None
        return self._img(f"profileicon/{icon_id}.png", f"{DD}/cdn/{self.ver}/img/profileicon/{icon_id}.png")

    def champion(self, name: str):
        k = self.champ_key(name)
        return self._img(f"champion/{k}.png", f"{DD}/cdn/{self.ver}/img/champion/{k}.png")

    def _local_index(self) -> dict[str, str]:
        """素材库里默认皮肤（skin_id = champion_id × 1000）的 centered 原画路径，按英雄 key（小写）索引。
        一次渲染只查一次库；查不到库（没配数据库、表不存在）就当没有，不影响出图。"""
        if self._local is None:
            self._local = {}
            try:
                from lol_radar import psql   # 渲染层平时不碰数据库，这里只读一张素材表
                for key, path in psql(
                        "select champion_key, local_path from lol.asset_inventory "
                        "where skin_id = champion_id * 1000 and kind = 'splash' "
                        "and variant = 'centered' and status = 'present'"):
                    if key and path:
                        self._local[key.lower()] = path
            except BaseException:   # psql 失败时会 SystemExit
                pass
        return self._local

    def _local_splash(self, name: str):
        idx = self._local_index()
        # 库里的 champion_key 和对局里的 champion_name 大小写不一定一样（FiddleSticks / Fiddlesticks），按小写匹配
        for k in dict.fromkeys([name.lower(), self.champ_key(name).lower()]):
            p = idx.get(k)
            if p:
                try:
                    return Image.open(p).convert("RGB")
                except Exception:
                    return None
        return None

    def splash(self, name: str):
        """英雄默认原画（centered 版，1280×720，人物在画面中间），背景用。

        先找 NAS 素材库（lol.asset_inventory，从游戏文件解出来的，永远是当前版本）；
        库里没有再走 ddragon。ddragon 的文件名大小写和英雄 id 不一定一致，老英雄改版后
        还可能留着老原画（比如草人），所以只当兜底。
        ddragon 下载的缓存在 SPLASH_CACHE 下跨版本复用；下载失败记一个空标记，24 小时内不再重试，
        免得每张图都卡在超时上。拿不到返回 None。"""
        if not name:
            return None
        local = self._local_splash(name)
        if local is not None:
            return local
        k = self.champ_key(name)
        SPLASH_CACHE.mkdir(parents=True, exist_ok=True)
        p, miss = SPLASH_CACHE / f"{k}_0.jpg", SPLASH_CACHE / f"{k}_0.miss"
        if not p.exists():
            if miss.exists() and time.time() - miss.stat().st_mtime < 86400:
                return None
            # ddragon 原画的文件名大小写不一定和英雄 id 一致：草人当前原画在 FiddleSticks_0.jpg，
            # 而 Fiddlesticks_0.jpg 是改版前的老原画（centered 版 403，splash 版还留着老图）。
            # 所以先用库里的原名，再用规范化后的 id；centered 优先，都没有才退到 splash 版
            names = list(dict.fromkeys([name, k]))
            raw = None
            for kind in ("centered", "splash"):
                for n in names:
                    raw = _get(f"{DD}/cdn/img/champion/{kind}/{n}_0.jpg", timeout=15)
                    if raw:
                        break
                if raw:
                    break
            if not raw:
                miss.touch()
                return None
            miss.unlink(missing_ok=True)
            tmp = p.with_suffix(".tmp")
            tmp.write_bytes(raw)
            tmp.replace(p)   # 原子替换：并发出图时不会读到半张
        try:
            return Image.open(p).convert("RGB")
        except Exception:
            return None

    def item(self, item_id: int):
        if not item_id:
            return None
        return self._img(f"item/{item_id}.png", f"{DD}/cdn/{self.ver}/img/item/{item_id}.png")

    def rune(self, rune_id: int):
        icon = self.runes().get(int(rune_id or 0))
        if not icon:
            return None
        return self._img(f"perk/{rune_id}.png", f"{DD}/cdn/img/{icon}")

    def spell(self, spell_id: int):
        f = self.spells().get(int(spell_id or 0))
        if not f:
            return None
        return self._img(f"spell/{f}", f"{DD}/cdn/{self.ver}/img/spell/{f}")
