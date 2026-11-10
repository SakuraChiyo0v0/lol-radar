# lol-radar

英雄联盟「能力雷达 / 对位卡」出图工具。把玩家近期对局按**同位置百分位**归一成八维雷达，算一个 0–10 的综合评分和称号，画成 1600×900 的转播风格卡片。

![单局对位示例](docs/match.png)

八个维度：伤害占比、伤害转化、经济每分、补刀每分、参团率、视野每分、KDA、承伤占比。每一维都和**同位置**的所有玩家比，50 = 中位数。

## 模式 × 队列

出什么图由两件事决定，两者自由组合：

- `--mode`：图怎么排
- `--queue`：和谁比，也就是取哪个队列的对局、比较池用哪个队列的人

| 模式 | 用途 | 中间那块显示 |
|---|---|---|
| `duel` | 1–2 人近期对比（只给一个人就是个人雷达） | 常用英雄 |
| `match` | 某一局里同位置两人的本场发挥（队列按那一局自己的） | 英雄 / 召唤师技能 / 符文 / 装备 / 饰品 |
| `champ` | 只比同一个英雄的对局 | 该英雄的局数和胜率 |
| `overall` | 整体实力：五个位置分别算，再按局数加权 | 上野中下辅五格评分 |
| `team` | 5v5 全队战力（库里的对局，或读客户端实时对局） | 每路一行 |
| `aram` | `duel` + 乱斗队列的简写 | 常用英雄 |

| 队列 | 对局 | 比较池 |
|---|---|---|
| `rift`（默认） | 峡谷全部分路局 | 同位置 |
| `solo` | 单双排（420） | 单双排同位置 |
| `flex` | 灵活排位（440） | 灵活排位同位置 |
| `ranked` | 单双排 + 灵活 | 排位同位置 |
| `normal` | 匹配（400 / 430 / 480 / 490） | 匹配同位置 |
| `aram` | 普通大乱斗（450） | 大乱斗全体，不分位置 |
| `kiwi` | 海克斯乱斗（2400） | 海克斯乱斗全体，不分位置 |

乱斗没有分路，所以 `overall` 和 `team` 不能配乱斗队列，乱斗也不能指定 `--position`。K/D/A、常用英雄、近 10 局都只算所选队列。比较池少于 200 人时，图右上角会标「比较池小 · 仅供参考」。

```bash
python3 lol_radar.py --players "Player#1234" "Rival#5678" --position ADC              # 峡谷下路对比
python3 lol_radar.py --players "Player#1234" "Rival#5678" --queue ranked             # 排位对比
python3 lol_radar.py --players "Player#1234" "Rival#5678" --queue kiwi               # 海克斯乱斗对比
python3 lol_radar.py --mode champ --champion 伊泽瑞尔 --players "Player#1234" --queue solo
python3 lol_radar.py --mode overall --players "Player#1234" --days 90 --queue ranked
python3 lol_radar.py --mode match --match-id HN1_1234567890 --players "Player#1234"
python3 lol_radar.py --mode team --match-id HN1_1234567891
```

`python3 lol_radar.py --help` 看全部参数。脚本最后一行会打印一行 JSON 摘要（各人各维的分位），方便别的程序接着用。

## 依赖

- Python 3.10+，[Pillow](https://pypi.org/project/pillow/)
- PostgreSQL 里的对局数据（见下面「数据从哪来」），以及能连上它的 `psql`
- 一个中文字体（默认微软雅黑 `msyh.ttc`），一个数字字体（默认 Bebas Neue，可选）
- 英雄、装备、符文图标从 Riot 官方的 [Data Dragon](https://developer.riotgames.com/docs/lol#data-dragon) 下载，首次用到时缓存到本地

```bash
pip install pillow
```

## 配置

所有和部署环境有关的东西都走环境变量，每一项都有默认值。完整列表和说明见 [`.env.example`](.env.example)，集中定义在 [`lol_card/config.py`](lol_card/config.py)。

最少要配的两项：

```bash
export LOL_RADAR_DB_URL=postgresql://user:password@localhost:5432/lol   # 或用 LOL_RADAR_DB_URL_FILE 指向一个存连接串的文件
export LOL_RADAR_PSQL=psql                                             # 默认是 docker exec postgres psql
```

如果字体、缓存不想放在默认的 `/opt/data` 下，设 `LOL_RADAR_HOME` 一项就能把它们整体挪走，也可以逐项单独设。

## 数据从哪来

这个仓库只负责「读库 + 出图」，不负责抓数据。它读的是 PostgreSQL 里 `lol` schema 下的几张表，用到的列如下：

| 表 | 用到的列 |
|---|---|
| `lol.match` | `match_id`, `game_creation`, `queue_id` |
| `lol.match_participant` | `match_id`, `puuid`, `team_id`, `team_position`, `champion_name`, `champ_level`, `kills`, `deaths`, `assists`, `cs`, `gold_earned`, `damage_to_champ`, `damage_taken`, `vision_score`, `time_played`, `win`, `items`（7 格数组：6 格装备 + 饰品）, `spell1_id`, `spell2_id`, `perks`（Riot match-v5 的 perks JSON） |
| `lol.player` | `puuid`, `game_name`, `tag_line`, `profile_icon`（可选） |
| `lol.player_rank` | `puuid`, `tier`, `division`, `lp`, `updated_at` |

字段含义和 Riot match-v5 接口一致，用自己的采集程序把对局写进这几张表就能用。

## 目录

```
lol_radar.py         入口：读库、算分位、按模式组织数据，再交给渲染
lol_card/            渲染包（组件 + 模板）
  config.py          环境变量
  templates.py       每种模式的排布（改布局只改这里）
  widgets/           组件：头像、装备区、评分卡、雷达、全队行……
  README.md          渲染包内部说明：改什么去哪个文件
lol_radar_card.py    兼容入口，转发到 lol_card
lol_live_lobby.py    在装着客户端的 Windows 机器上运行，读当前选人 / 对局（team --live 用）
lol_radar_warm.sh    定时预热比较池，避免出图时现场重建
plugin/              Hermes Agent 插件：把出图做成一个模型可直接调用的工具
tools/               capture.py 录制渲染输入，compare.py 逐像素对比改动前后
```

## 改布局

元素都是独立组件，整页结构写在 [`lol_card/templates.py`](lol_card/templates.py)，不写坐标：每个组件报告自己要多高，`VStack` 把下一个接在上一个下面，`HStack` 从左往右分宽度。

```python
PLAYER_CARD = VStack([
    Avatar(), Gap(10), NameLine(), RankLine(), KdaLine(), PosLine(),
    OneOf([(has_items, Loadout()), (has_breakdown, PosBreakdown()), (None, TopChamps())]),
    Gap(30), ScoreBox(), Gap(44), RecentStrip(),
])

CARD_PAGE = VStack([
    Header(), Gap(28),
    HStack([(CARD_W, Side(0, PLAYER_CARD)), (None, Radar()), (CARD_W, Side(1, PLAYER_CARD))], pad=MARGIN),
])
```

- 放大装备区：改成 `Loadout(icon=48)`，下面的评分卡和近 10 局会自动往下让。
- 调距离：改中间的 `Gap`。
- 某个模式单独排：复制一份改好，登记到 `TEMPLATES`。

内容超出画布、或者某个元素比分到的宽度还宽，出图时会在 stderr 提示。细节见 [`lol_card/README.md`](lol_card/README.md)。

改完想确认没把别的模式画坏：

```bash
python3 tools/capture.py match --mode match --match-id HN1_xxx --players "Player#1234"   # 先录一次输入
git worktree add /tmp/base HEAD                                                          # 改动前的代码
python3 tools/compare.py /tmp/base                                                       # 逐像素对比
```

## 实时对局（`team --live`）

在游戏客户端所在的 Windows 机器上，`lol_live_lobby.py` 从 `LeagueClientUx.exe` 的启动参数里读出本地 LCU 端口和临时令牌，**只发只读的 GET 请求**，拿到当前选人或对局的 10 个人。

`lol_radar.py` 通过 ssh 把这个脚本拷过去跑一次，跑完就删。主机、ssh 配置、远端目录见 `.env.example` 里的 `LOL_RADAR_LIVE_*`。选人阶段客户端看不到对面是谁，所以对面只有位置。

## Hermes 插件

`plugin/` 是给 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 用的工具插件：模型传结构化参数（模式、玩家、位置、天数），插件校验后以参数列表起子进程调用 `lol_radar.py`，不经过 shell，最后返回一行 `MEDIA:<图片路径>`，由平台发出去。

把 `plugin/` 拷到 profile 的 `plugins/lol-radar/`，再在 `platform_toolsets` 里启用 `lol-radar` 工具集即可。`LOL_RADAR_SCRIPT` 指向 `lol_radar.py` 的位置。

## 免责声明

本项目与 Riot Games 无关，也未获得其认可。英雄联盟（League of Legends）及相关名称、图标归 Riot Games, Inc. 所有。

## 协议

[MIT](LICENSE)
