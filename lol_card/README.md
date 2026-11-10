# lol_card：能力雷达 / 对位卡渲染

`lol_radar.py` 取数，这个包只画。旧入口 `lol_radar_card.py` 只是转发，调用方不用改。

## 想改什么，去哪改

| 想做的事 | 改哪里 |
|---|---|
| 元素的顺序、间距、某个模式显示什么、整页结构 | `templates.py` |
| 某个元素的尺寸 | 在 `templates.py` 里给组件传参数，比如 `Loadout(icon=44)` |
| 某个元素里面长什么样 | `widgets/` 里对应的组件 |
| 配色、字体、画布尺寸 | `theme.py` |
| 评分权重、称号 | `scoring.py` |
| 路径、数据库、缓存 | 环境变量，见仓库根目录 `.env.example` |

## 排布怎么写

模板里不写坐标。每个组件报告自己要多高，容器按顺序摆：

```python
PLAYER_CARD = VStack([
    Avatar(),
    Gap(10),
    NameLine(),
    RankLine(),
    KdaLine(),
    PosLine(),
    OneOf([                                  # 中段按数据三选一
        (has_items, Loadout()),              # 单局：装备区
        (has_breakdown, PosBreakdown()),     # 整体模式：分位置评分
        (None, TopChamps()),                 # 其余：常用英雄
    ]),
    Gap(30),
    ScoreBox(),
    Gap(44),
    RecentStrip(),
])

CARD_PAGE = VStack([
    Header(),
    Gap(28),
    HStack([
        (CARD_W, Side(0, PLAYER_CARD)),      # 左：第 1 个人
        (None, Radar()),                     # 中：剩下的宽度给雷达
        (CARD_W, Choose(two_players,         # 右：两人时第 2 个人，一人时八维明细
                        Side(1, PLAYER_CARD),
                        Side(0, DetailPanel()))),
    ], pad=MARGIN),
])
```

| 容器 | 作用 |
|---|---|
| `VStack([...], gap=, pad=)` | 从上往下排，每个子节点接在上一个下面 |
| `HStack([(宽, 节点), ...], gap=, pad=)` | 从左往右排；宽写 `None` 表示分走剩下的宽度 |
| `Gap(n)` | 留 n 像素空白 |
| `Side(i, 节点)` | 把第 i 个玩家交给节点画，颜色和朝向按第 i 侧 |
| `OneOf([(条件, 节点), ...])` | 按玩家数据选第一个满足条件的；`None` 是兜底。高度取所有候选里最高的，所以不同模式下面的元素位置一致 |
| `Choose(条件, a, b)` | 按整页情况二选一，比如有几个人 |
| `Lanes(节点)` / `TeamSide(i, 节点)` | 5v5 用：每路画一行 / 取这一路第 i 队的人 |

常见改法：

- **放大某个元素**：改它的参数，比如 `Loadout(icon=44)`，下面的元素自动往下让。
- **调距离**：改两个元素中间的 `Gap`。
- **换顺序**：调整列表里的顺序。
- **某个模式单独排**：复制一份 `PLAYER_CARD` / `CARD_PAGE` 改好，登记到 `TEMPLATES["match"] = MATCH_PAGE`，调用时传 `template="match"`，或在 meta 里写 `"template": "match"`。
- **新增一种元素**：在 `widgets/` 里写一个 `Node` 子类，实现 `measure()`（返回高度，固定高度的直接写 `height` 字段）和 `draw(ctx, p, box)`，然后放进模板。

画完会检查两件事，有问题往 stderr 打一行提示，图照样出：

- 内容超出画布底部（会被裁掉）
- 某个组件比分到的宽度还宽（会压到旁边的元素上）

## 组件

| 组件 | 内容 |
|---|---|
| `Header` | 左上标题 + 右上徽标 |
| `Avatar` `NameLine` `RankLine` `KdaLine` `PosLine` | 玩家卡上半部分 |
| `Loadout` | 英雄 / 召唤师技能 / 符文 / 6 格装备 / 饰品（单局） |
| `TopChamps` | 常用英雄（历史模式） |
| `PosBreakdown` | 上野中下辅五格评分（整体模式） |
| `ScoreBox` | 综合评分 + 称号 + 四项小条 |
| `RecentStrip` | 近 10 局英雄 |
| `DetailPanel` | 八维明细（单人模式右栏） |
| `Radar` | 雷达（整页组件，画全部玩家） |
| `TeamPowerBar` `TeamRow` `LaneCenter` | 5v5 全队卡 |

组件不知道自己在哪一侧、第几行。侧别由 `ctx.idx` 给（0 左蓝，1 右红），位置由排布层给。

## 改完怎么验证

先用 `tools/capture.py` 把各模式实际传给渲染层的数据录下来，改完代码后用 `tools/compare.py`：同一份数据，改动前和改动后各画一张，逐像素对比。

- 重构这类不该改变画面的改动：预期差异像素为 0。
- 改排布：预期会有差异，打开 `new_*.png` 看效果即可。
