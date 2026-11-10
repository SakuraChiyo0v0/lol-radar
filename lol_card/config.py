"""所有和部署环境有关的设置都在这里，全部可以用环境变量覆盖。

默认值按作者的部署写（Hermes 容器里 /opt/data 为数据目录），换环境时只设环境变量，不改代码。
完整列表和说明见仓库根目录的 .env.example。
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    v = os.environ.get(name, "").strip()
    return v or default


def _path(name: str, default: str | Path) -> Path:
    return Path(_env(name, str(default)))


# 根目录：其余路径默认都挂在它下面
HOME = _path("LOL_RADAR_HOME", "/opt/data")
CACHE_DIR = _path("LOL_RADAR_CACHE_DIR", HOME / "cache" / "lol")

# ---- 数据库
# 连接串二选一：直接给 LOL_RADAR_DB_URL，或给一个存着连接串的文件（文件更适合放密码）
DB_URL = _env("LOL_RADAR_DB_URL", "")
DB_URL_FILE = _path("LOL_RADAR_DB_URL_FILE", "/opt/astra/home/db-url")
# psql 怎么跑：本机有 psql 就设成 "psql"；数据库在别的容器里就设成 "docker exec <容器名> psql"
PSQL_CMD = _env("LOL_RADAR_PSQL", "docker exec postgres psql").split()
DB_TIMEOUT = int(_env("LOL_RADAR_DB_TIMEOUT", "600"))

# ---- 缓存
DDRAGON_CACHE = _path("LOL_RADAR_DDRAGON_CACHE", CACHE_DIR / "ddragon")
POOL_DIR = _path("LOL_RADAR_POOL_DIR", CACHE_DIR / "radar_pool")
QUANT_FILE = _path("LOL_RADAR_QUANT_FILE", CACHE_DIR / "radar_quantiles.json")
CACHE_TTL = int(_env("LOL_RADAR_CACHE_TTL", str(12 * 3600)))   # 比较池 / 分位曲线缓存秒数
DDRAGON_FALLBACK_VERSION = _env("LOL_RADAR_DDRAGON_VERSION", "16.22.1")  # 连不上 ddragon 时用

# ---- 字体
FONT = _path("LOL_RADAR_FONT", HOME / "fonts" / "msyh.ttc")
FONT_BOLD_INDEX = int(_env("LOL_RADAR_FONT_BOLD_INDEX", "1"))   # .ttc 里粗体字面的序号；单字面字体设 0
FONT_NUM = _path("LOL_RADAR_FONT_NUM", HOME / "fonts" / "BebasNeue-Regular.ttf")

# ---- 输出
OUT_DIR = _path("LOL_RADAR_OUT_DIR", HOME / "cache" / "charts")
DEFAULT_OUT = _path("LOL_RADAR_OUT", OUT_DIR / "lol_radar.png")

# ---- 实时对局（team --live）：通过 ssh 到装着客户端的 Windows 机器读 LCU
LIVE_SSH_HOST = _env("LOL_RADAR_LIVE_HOST", "pc")                 # ssh 主机名或别名
LIVE_SSH_CONFIG = _env("LOL_RADAR_LIVE_SSH_CONFIG", str(HOME / ".ssh" / "config"))  # 设成空串则用 ssh 默认配置
LIVE_REMOTE_DIR = _env("LOL_RADAR_LIVE_REMOTE_DIR", "C:/LocalSpace")  # 远端临时放脚本的目录
LIVE_REMOTE_PYTHON = _env("LOL_RADAR_LIVE_PYTHON", "python")
