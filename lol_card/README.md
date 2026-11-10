# lol_card：能力雷达 / 对位卡渲染

`lol_radar.py` 取数，这个包只画。旧入口 `lol_radar_card.py` 只是转发，调用方不用改。

## 想改什么，去哪改

| 想做的事 | 改哪里 |
|---|---|
| 换配色、字体、画布尺寸 | `theme.py` |
| 某个元素长什么样（装备区图标多大、评分卡显示哪几项小条） | `widgets/` 里对应的组件，或在 `templates.py` 里给组件传参数，比如 `Loadout(icon=44)` |
| 元素的上下顺序、放多高、某个模式显示 / 不显示 | `templates.py` |
| 评分权重、称号 | `scoring.py` |

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
| `Radar` | 中间雷达（整页组件） |
| `TeamPowerBar` `TeamRow` `LaneCenter` | 5v5 全队卡 |

每个组件都是一个小 dataclass：构造参数是样式，`draw(ctx, p, box)` 在 `box` 里画玩家 `p`。
组件不知道自己在哪一侧、第几行。侧别由 `ctx.idx` 给（0 左蓝，1 右红），位置由模板给。

## 排布怎么写

玩家卡是一列从上到下的 `At(y, 组件)`：

```python
PLAYER_SLOTS = [
    At(124, Avatar()),
    At(266, NameLine()),
    ...
    At(414, Loadout(), when=_has_items),     # 单局才有
    At(414, TopChamps(), when=_champs),      # 历史模式
    At(528, ScoreBox()),
    At(760, RecentStrip()),
]
```

给某个模式单独排：复制一份 slots 改顺序或 y，登记到 `TEMPLATES["match"] = MATCH_SLOTS`，
调用 `render(..., template="match")`，或者在 meta 里写 `"template": "match"`。

## 改完怎么验证

仓库里的 `tools/compare.py`：先用 `tools/capture.py` 把各模式实际传给渲染层的数据录下来，
之后改完代码，用同一份数据把改动前和改动后各画一张，逐像素对比。只改排布时预期会有差异，
打开 `new_*.png` 看效果即可。重构这类不该改变画面的改动，预期差异像素为 0。
