#!/usr/bin/env bash
# scan_all.sh — 按顺序跑扫描:新源 + 老基线
# 用法:
#   bash src/setup/scan_all.sh new      # 只跑新源(public_databases_v2/)
#   bash src/setup/scan_all.sh legacy   # 只跑老基线(public_databases/)
#   bash src/setup/scan_all.sh all      # 全跑
#   bash src/setup/scan_all.sh smoke    # 冒烟:每个源 --limit 100
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCAN="python3 $ROOT/src/setup/scan_protein.py"

run_new() {
  echo "=== 新源: pdb → uniprot_sprot → uniref90 → uniprot_trembl ==="
  $SCAN --source pdb            "${LIMIT:+--limit $LIMIT}"
  $SCAN --source uniprot_sprot  "${LIMIT:+--limit $LIMIT}"
  $SCAN --source uniref90       "${LIMIT:+--limit $LIMIT}"
  $SCAN --source uniprot_trembl "${LIMIT:+--limit $LIMIT}"
}

run_legacy() {
  echo "=== 老基线 ==="
  $SCAN --legacy legacy_pdb_2022            "${LIMIT:+--limit $LIMIT}"
  $SCAN --legacy legacy_uniprot_all_2021    "${LIMIT:+--limit $LIMIT}"
  $SCAN --legacy legacy_uniref90_2022       "${LIMIT:+--limit $LIMIT}"
  $SCAN --legacy legacy_rfam_2022           "${LIMIT:+--limit $LIMIT}"
  $SCAN --legacy legacy_mgy_2022            "${LIMIT:+--limit $LIMIT}"
  $SCAN --legacy legacy_bfd_2022            "${LIMIT:+--limit $LIMIT}"
}

MODE="${1:-all}"
LIMIT="${LIMIT:-}"

case "$MODE" in
  new)    run_new ;;
  legacy) run_legacy ;;
  all)    run_new; run_legacy ;;
  smoke)
    LIMIT="${LIMIT:-100}" run_new
    LIMIT="${LIMIT:-100}" run_legacy
    ;;
  *) echo "usage: $0 {new|legacy|all|smoke}  [LIMIT=N]" >&2; exit 2 ;;
esac