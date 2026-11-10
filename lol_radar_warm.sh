#!/bin/sh
# 雷达图样本池预热：提前重建 7 天 / 30 天两份同位置池子，让出图不撞冷启动。
#
# 为什么要这条：池子缓存 12 小时有效（LOL_RADAR_CACHE_TTL），过期后第一个出图的人
# 要等全表重建（7 天约 45 秒、30 天约 75 秒）。这里每 11 小时主动重建一次，赶在过期前。
# 只重建池子，不出图；静默（stdout 为空），失败写日志。
#
# 环境变量：
#   LOL_RADAR_HOME       数据根目录，默认 /opt/data
#   LOL_RADAR_SCRIPTS    lol_radar.py 所在目录，默认本脚本所在目录
#   LOL_RADAR_WARM_LOG   日志文件，默认 $LOL_RADAR_HOME/logs/lol-radar-warm.log
#   LOL_RADAR_WARM_STOP  这个文件存在就跳过，默认 $LOL_RADAR_HOME/cache/lol/STOP_RADAR_WARM
#   RADAR_WARM_DAYS      要预热的天数窗口，默认 "7 30"
set -u
HOME_DIR=${LOL_RADAR_HOME:-/opt/data}
SCRIPTS=${LOL_RADAR_SCRIPTS:-$(cd "$(dirname "$0")" && pwd)}
LOG=${LOL_RADAR_WARM_LOG:-$HOME_DIR/logs/lol-radar-warm.log}
STOP=${LOL_RADAR_WARM_STOP:-$HOME_DIR/cache/lol/STOP_RADAR_WARM}
PY=${LOL_RADAR_PYTHON:-python3}
mkdir -p "$(dirname "$LOG")"

[ -f "$STOP" ] && { echo "$(date '+%F %T') STOP_RADAR_WARM 存在，跳过" >> "$LOG"; exit 0; }

cd "$SCRIPTS" || exit 0
for d in ${RADAR_WARM_DAYS:-7 30}; do
  s=$(date +%s)
  if OUT=$(LOL_RADAR_SCRIPTS="$SCRIPTS" "$PY" -c "
import os, sys
sys.path.insert(0, os.environ['LOL_RADAR_SCRIPTS'])
import lol_radar as R
p = R.Pool($d, refresh=True)
print(' '.join(f'{k}={p.count(k)}' for k in R.POSITIONS))
" 2>&1); then
    echo "$(date '+%F %T') d$d ok $(( $(date +%s) - s ))s $OUT" >> "$LOG"
  else
    echo "$(date '+%F %T') d$d FAIL $(( $(date +%s) - s ))s $(echo "$OUT" | tail -1 | cut -c1-200)" >> "$LOG"
  fi
done
tail -n 200 "$LOG" > "$LOG.tmp" 2>/dev/null && mv "$LOG.tmp" "$LOG"
exit 0
