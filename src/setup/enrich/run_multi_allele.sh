#!/usr/bin/env bash
# run_multi_allele.sh
# Step B: 23-allele 全量重算 mhcflurry, 每个 allele 独立 checkpoint。
# 派生 tool 名(沿用 anoxpepred-frs/chelating 派生约定)入库,
# 不动 baseline tool='mhcflurry'。
#
# 用法:
#   bash src/setup/enrich/run_multi_allele.sh         # 全量跑
#   bash src/setup/enrich/run_multi_allele.sh --limit 50000   # 限 5w 条烟测
#   bash src/setup/enrich/run_multi_allele.sh --alleles "A*02:01,A*11:01"   # 子集
#
# 服务端必须已在跑(mhcflurry service.py 已加载支持 allele 透传的新代码)。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"

ALLELE_FILE="src/setup/enrich/conf/mhcflurry_panel.tsv"

# 解析 --alleles 子集(可选)
EXTRA_ALLELES=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --limit) LIMIT_FLAG="--limit $2"; shift 2 ;;
        --batch) BATCH_FLAG="--batch $2"; shift 2 ;;
        --alleles) EXTRA_ALLELES="$2"; shift 2 ;;
        --concurrent) CONCURRENT_FLAG="--concurrent $2"; shift 2 ;;
        *) echo "unknown arg: $1"; exit 1 ;;
    esac
done

LIMIT_FLAG="${LIMIT_FLAG:-}"
BATCH_FLAG="${BATCH_FLAG:---batch 1000}"
CONCURRENT_FLAG="${CONCURRENT_FLAG:-}"

# 抽 allele 列表
if [[ -n "$EXTRA_ALLELES" ]]; then
    ALLELES="$EXTRA_ALLELES"
else
    # 从 tsv 抽第一个字段, 跳过注释行(#) 和表头(allele)
    ALLELES=$(awk -F'\t' '!/^#/ && NR>1 && $1 != "allele" {print $1}' "$ALLELE_FILE" | paste -sd,)
fi

echo "===== mhcflurry multi-allele run ====="
echo "alleles: $ALLELES"
echo "limit:   ${LIMIT_FLAG:-<none>}"
echo "batch:   $BATCH_FLAG"
echo "concurrent: ${CONCURRENT_FLAG:-<single client>}"
echo "======================================"
echo

PYTHONPATH=src python3 -u src/setup/enrich/enrich.py \
    --tool mhcflurry \
    --alleles "$ALLELES" \
    $BATCH_FLAG \
    $CONCURRENT_FLAG \
    $LIMIT_FLAG