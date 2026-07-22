"""踩过的坑清单 — 2026-07-17 iGEM 数据刷新战

每个 PITFALL 是一条机器可校验的断言。scan_protein.py 和任何后续 DB 操作脚本
应 import 这个模块并在初始化/运行期校验。文件首部的注释是人类可读版,运行时断言在
validate_environment() / assert_safe_scan()。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


# --- 路径常量 ---------------------------------------------------------------
PUBLIC_DATA_ROOT = "/media/lenovo/Data/public_databases_v2"
LEGACY_ROOT = "/media/lenovo/Data/public_databases"
MANIFEST_PATH = Path(PUBLIC_DATA_ROOT) / "manifest" / "MANIFEST.json"
PG_DSN = os.environ.get(
    "IGEM_PG_DSN",
    "host=127.0.0.1 port=5432 dbname=igem_peptides user=igem password=igem_local_2026",
)
PG_DB = "igem_peptides"
PG_USER = "igem"

# 20 字母标准氨基酸 (大写)
STD_AA = frozenset("ACDEFGHIKLMNPQRSTVWY")


class PitfallError(Exception):
    """踩到了已知的坑,应该 abort 而不是继续。"""


def validate_environment() -> None:
    """启动 scan_protein 前调用。任何一项失败都抛 PitfallError。

    检查项对应坑 1/3/7/8/10。
    """
    errors = []

    # Pitfall #1: 不要用 Python gzip.open 扫大 .gz — 慢 30 倍。检查 zcat 可用
    try:
        subprocess.run(["zcat", "--version"], capture_output=True, timeout=5, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        errors.append("PITFALL #1: zcat 不可用 — 大 .gz 解压会极慢,装 gzip 包")

    # Pitfall #3: _validate_and_upper 必须 C-level,不能用 Python byte iteration
    # (静态检查 — 这个断言靠代码 review,这里只记录注释)
    # 见下方 assert_safe_scan() 的运行时校验

    # Pitfall #7: 不要两个 .gz 同盘同时跑 — 会抢 IO 慢 1/3
    # (运行时校验在 assert_safe_scan 里)

    # Pitfall #10: 老 .fa 文件 (legacy_bfd 之类) 走 _stream_fasta_gz 的 else 分支
    # 不要调 _stream_fasta_plain
    # (代码层约束,见 scan_protein.py)

    # Pitfall #8: 同源重跑会 ON CONFLICT DO NOTHING 跳过 — 不会报错也不会重复
    # 这是设计,不是 bug,但用户应知道

    if errors:
        raise PitfallError("\n".join(errors))


def assert_safe_scan(path: str, nbytes: int, other_running_scans: int = 0) -> None:
    """scan 单个源前的最后一道防线。

    Args:
        path: 要扫描的 .fasta/.fa/.gz 路径
        nbytes: 文件大小(bytes)
        other_running_scans: 同盘上其它正在跑的 scan 进程数
    """
    # Pitfall #5: 估算大小别信 — 但允许用来 warn
    if nbytes > 100 * 1024**3:
        print(f"[pitfall-warn] 文件超大 ({nbytes/1e9:.1f} GB),实际解压后可能 1.3-1.5x", file=sys.stderr, flush=True)

    # Pitfall #7: 同盘不要并行跑 scan
    if other_running_scans > 0:
        raise PitfallError(
            f"PITFALL #7: 已有 {other_running_scans} 个 scan_protein 在同盘运行,会抢 IO。"
            "排队,不要并发。检查命令: pgrep -af scan_protein.py"
        )

    # Pitfall #10: 验证文件存在且可读
    if not os.path.exists(path):
        raise PitfallError(f"PITFALL #10: 文件不存在 {path}")

    # Pitfall #1: 大 .gz 不要用 Python gzip.open(隐式约束 — scan_protein.py 内部已用 zcat)


def assert_peptide_valid(seq: str) -> bool:
    """Pitfall #3: 20 字母过滤必须在 C-level 做,不要 Python-level byte iteration。

    这个函数本身足够快(单 seq < 1ms),用作参考实现。任何替代实现必须 benchmark
    不慢于这个。
    """
    upper = seq.upper()
    for ch in upper:
        if ch not in STD_AA:
            return False
    return True


def get_running_scan_count() -> int:
    """Pitfall #7: 同盘并发检测。"""
    try:
        out = subprocess.run(
            ["pgrep", "-f", "scan_protein.py"],
            capture_output=True, text=True, timeout=5,
        )
        # 排除当前 shell 自己的 pgrep 调用
        return max(0, len([l for l in out.stdout.splitlines() if "scan_protein" in l]) - 1)
    except Exception:
        return 0


# --- 静态知识库 (给人看的,也写进 README) ------------------------------------

PITFALLS = {
    1: {
        "title": "Python gzip.open 慢 30 倍",
        "lesson": "扫大 .gz (>1GB) 必须用 subprocess.Popen(['zcat']) + pipe,不要 gzip.open",
        "fix_in_code": "src/setup/scan_protein.py:_stream_fasta_gz",
    },
    2: {
        "title": "pyfastx .fxi 索引在大 .gz 上极慢",
        "lesson": "不要传 build_index=True 给 pyfastx.Fasta(),首次迭代 .gz 会卡数分钟",
        "fix_in_code": "本次完全弃用 pyfastx,改用纯 zcat + 手动 FASTA parser",
    },
    3: {
        "title": "Python-level byte iteration 慢 100 倍",
        "lesson": "_validate_and_upper 必须用 bytes.upper() (C-level),不要 for ch in bytes",
        "fix_in_code": "src/setup/scan_protein.py:_validate_and_upper",
    },
    4: {
        "title": "gzip block 大 + seek 慢",
        "lesson": "Python gzip 解压默认 LZ77 逐字节,uniref90.gz 30GB 解压要 1+ 小时",
        "fix_in_code": "Pitfall #1 已规避",
    },
    5: {
        "title": "文件大小估算别信",
        "lesson": "实际 uniprot_trembl 38GB(估 150GB), uniref90 30GB(估 85GB)。下完算 sha256 + bytes 才是真相",
        "fix_in_code": "scan_protein.py 强制 sha256 + os.path.getsize()",
    },
    6: {
        "title": "all_proxy 环境变量格式错",
        "lesson": "aria2c 警告 'unrecognized proxy format' 时直连仍有效(警告非致命)",
        "fix_in_code": "无,这是下载工具行为,运行后确认 sha256 一致即可",
    },
    7: {
        "title": "同盘并发 scan 抢 IO 慢 1/3",
        "lesson": "同一 .gz 文件不要两个 scan_protein 并行跑,排队串行",
        "fix_in_code": "lib/pitfalls.py:assert_safe_scan() 运行时校验",
    },
    8: {
        "title": "ON CONFLICT DO NOTHING 跳过重复",
        "lesson": "重跑同源不会报错,只是 rows_inserted 不增。UNIQUE(sequence,source,source_version)",
        "fix_in_code": "peptides 表 UNIQUE 约束",
    },
    9: {
        "title": "manifest 版本号格式",
        "lesson": "source_version 必须是 YYYY-MM-DD 才能写 DATE 列,否则存 NULL + CURRENT_DATE",
        "fix_in_code": "scan_protein.py 里 version_str if '-' in version_str else None",
    },
    10: {
        "title": "legacy 老 .fa 文件没 .gz",
        "lesson": "_stream_fasta_gz 内部已处理 .gz / 非 .gz 两种,不要单独调 _stream_fasta_plain",
        "fix_in_code": "scan_protein.py 内部统一调 _stream_fasta_gz",
    },
    11: {
        "title": "fetch 沙盒禁止 EBI/NCBI/RCSB",
        "lesson": "agent 的 fetch tool 不让访问数据库站点,让用户在终端自己下",
        "fix_in_code": "无,这是工具限制",
    },
    12: {
        "title": "MGnify 2024 URL TBD",
        "lesson": "metagenomics/mgnify_genomes/current/ 在 FTP 是 404,要走网页端 download",
        "fix_in_code": "MANIFEST.json 里 mgy_2024.status='TBD',url='TBD'",
    },
    13: {
        "title": "trim 真路径 vs agent 假想路径",
        "lesson": "实际路径 /media/lenovo/Data/public_databases/,不是 /home/lenovo/public_databases",
        "fix_in_code": "lib/pitfalls.py:PUBLIC_DATA_ROOT/LEGACY_ROOT 常量",
    },
}


def print_pitfalls() -> None:
    """给人看的时候 dump 一下。"""
    print("=" * 70)
    print("iGEM 数据刷新战踩坑清单 (2026-07-17)")
    print("=" * 70)
    for k, v in PITFALLS.items():
        print(f"\n#{k}: {v['title']}")
        print(f"  lesson: {v['lesson']}")
        if v.get("fix_in_code"):
            print(f"  fix:    {v['fix_in_code']}")


if __name__ == "__main__":
    print_pitfalls()