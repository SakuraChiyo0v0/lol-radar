#!/bin/sh
# 雷达图样本池预热：提前重建比较池，让出图不撞冷启动。
#
# 为什么要这条：池子缓存 12 小时有效（LOL_RADAR_CACHE_TTL），过期后第一个出图的人
# 要等全表重建（7 天约 45 秒、30 天约 75 秒）。这里每 11 小时主动重建一次，赶在过期前。
# 只重建池子，不出图；静默（stdout 为空），失败写日志。
#
# 预热哪些池子 = 队列 × 天数。默认 rift 队列预热 7 天和 30 天（rift 池里顺带建了两个乱斗池），
# 其余队列只预热 30 天（--queue 出图默认用 30 天池）。一个队列失败不影响其它队列。
#
# 环境变量：
#   LOL_RADAR_HOME       数据根目录，默认 /opt/data
#   LOL_RADAR_SCRIPTS    lol_radar.py 所在目录，默认本脚本所在目录
#   LOL_RADAR_WARM_LOG   日志文件，默认 $LOL_RADAR_HOME/logs/lol-radar-warm.log
#   LOL_RADAR_WARM_STOP  这个文件存在就跳过，默认 $LOL_RADAR_HOME/cache/lol/STOP_RADAR_WARM
#   RADAR_WARM_POOLS     要预热的「队列:天数」列表，默认 "rift:7 rift:30 ranked:30 solo:30 flex:30"
#                        （normal 局太少，预热意义不大；aram/kiwi 在 rift 池里，不用单独写）
#   RADAR_WARM_DAYS      旧写法：只给天数，等价于 rift:<天数>。设了 RADAR_WARM_POOLS 时忽略
set -u
HOME_DIR=${LOL_RADAR_HOME:-/opt/data}
SCRIPTS=${LOL_RADAR_SCRIPTS:-$(cd "$(dirname "$0")" && pwd)}
LOG=${LOL_RADAR_WARM_LOG:-$HOME_DIR/logs/lol-radar-warm.log}
STOP=${LOL_RADAR_WARM_STOP:-$HOME_DIR/cache/lol/STOP_RADAR_WARM}
PY=${LOL_RADAR_PYTHON:-python3}
mkdir -p "$(dirname "$LOG")"

[ -f "$STOP" ] && { echo "$(date '+%F %T') STOP_RADAR_WARM 存在，跳过" >> "$LOG"; exit 0; }

if [ -n "${RADAR_WARM_POOLS:-}" ]; then
  POOLS=$RADAR_WARM_POOLS
elif [ -n "${RADAR_WARM_DAYS:-}" ]; then
  POOLS=$(for d in $RADAR_WARM_DAYS; do printf 'rift:%s ' "$d"; done)
else
  POOLS="rift:7 rift:30 ranked:30 solo:30 flex:30"
fi

cd "$SCRIPTS" || exit 0
for spec in $POOLS; do
  q=${spec%%:*}
  d=${spec#*:}
  # 名字沿用原来的写法：rift 记成 d7 / d30，其余记成 ranked-d30 这种
  tag=$([ "$q" = rift ] && echo "d$d" || echo "$q-d$d")
  s=$(date +%s)
  if OUT=$(LOL_RADAR_SCRIPTS="$SCRIPTS" WARM_Q="$q" WARM_D="$d" "$PY" -c "
import os, sys
sys.path.insert(0, os.environ['LOL_RADAR_SCRIPTS'])
import lol_radar as R
q = R.QUEUES[os.environ['WARM_Q']]
p = R.Pool(int(os.environ['WARM_D']), refresh=True, queue=q)
print(' '.join(f'{k}={p.count(k)}' for k in R.POSITIONS))
" 2>&1); then
    echo "$(date '+%F %T') $tag ok $(( $(date +%s) - s ))s $OUT" >> "$LOG"
  else
    echo "$(date '+%F %T') $tag FAIL $(( $(date +%s) - s ))s $(echo "$OUT" | tail -1 | cut -c1-200)" >> "$LOG"
  fi
done
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit 0
