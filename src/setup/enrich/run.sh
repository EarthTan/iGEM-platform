#!/usr/bin/env bash
# run.sh — 串行调度 9 个工具,每个跑完才进下一个。
# 必须先到仓库根目录启动: cd /home/lenovo/Projects/iGEM-platform

set -uo pipefail

ROOT="/home/lenovo/Projects/iGEM-platform"
LOG_DIR="$ROOT/logs/enrich_run"
mkdir -p "$LOG_DIR"

# 工具顺序:fast + GPU 串行。
# ToxinPred3(慢)放最后,这样先看到 fast + 大部分 GPU 工具的产出,
# 如果决定中止 ToxinPred3,损失时间 ≤ 18天 也不会突然出现。
# CONCURRENT_PER_TOOL: hemopi2 GPU 8 并发可从 70→100 seq/s(+50%);其他工具
# 默认 1(client 已经是 fast path,并发对 CPU 类帮助不大)。
declare -A CONC=(
    [sodope]=1 [tipred]=1 [algpred2]=1 [anoxpepred]=1 [mhcflurry]=1
    [hemopi2]=1 [plm4cpps]=1 [temstapro]=1 [toxinpred3]=1
)
ORDER=(
    "sodope"      # 1205 seq/sec, ~4.5h
    "tipred"      # 438,        ~12.5h
    "algpred2"    # 431,        ~12.7h
    "anoxpepred"  # 328,        ~16.7h
    "mhcflurry"   # 342, 5-15aa 子集, ~4h(只覆盖 ~5M 条)
    "hemopi2"     # 70,         ~80h(GPU ESM-2 t6 ceiling,实测无并发空间)
    "plm4cpps"    # 149,        ~37h
    "temstapro"   # 137,        ~42h
    "toxinpred3"  # 12.94,      ~18.5天
)

BATCH="${BATCH:-1000}"

cd "$ROOT"

for tool in "${ORDER[@]}"; do
    c="${CONC[$tool]:-1}"
    echo "[$(date +%FT%T)] ============================================"
    echo "[$(date +%FT%T)] START tool=$tool batch=$BATCH concurrent=$c"
    echo "[$(date +%FT%T)] ============================================"
    python3 -u src/setup/enrich/enrich.py \
        --tool "$tool" --batch "$BATCH" --concurrent "$c" \
        2>&1 | tee -a "$LOG_DIR/${tool}.log"
    rc=${PIPESTATUS[0]}
    if [[ $rc -ne 0 ]]; then
        echo "[$(date +%FT%T)] FAIL tool=$tool rc=$rc — 暂停,需要人工介入"
        exit "$rc"
    fi
done

echo "[$(date +%FT%T)] ALL DONE."
