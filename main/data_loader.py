"""
============================================================================
 数据加载模块 — 从 data/ 目录读取 FASTA 和 CSV 文件
============================================================================

本模块是流水线的最上游，负责把所有输入数据统一解析为标准 Python 结构。

输入文件（位于 ../data/）：
  data/silk.fasta    — 丝素蛋白骨架序列（1 条，约 346 aa）
  data/linker.fasta  — linker 序列库（10 条，含柔性和刚性 linker）
  data/function.csv  — 功能肽数据库（约 2.5 万条，含抗氧化/抗菌等标签）

FASTA 格式说明
--------------
FASTA 是最通用的生物序列格式：
  >标识行（以 > 开头）
  序列行（可跨多行，字母为单字母氨基酸代码）

同一文件可包含多条序列，每条以 > 开头分隔。

CSV 格式说明
-------------
function.csv 的关键列：
  sequence           — 氨基酸序列（单字母）
  is_antioxidant     — 是否抗氧化（0/1）
  is_antimicrobial   — 是否抗菌（0/1）
  is_antiglycation   — 是否抗糖化（0/1）
  is_collagen_stimulating — 是否促胶原（0/1）
  is_cell_penetrating — 是否细胞穿透（0/1）
  source_name        — 肽的来源名称（如蛋白名+位置）
  database_id        — 数据库 ID
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

# DATA_DIR 定位到项目根目录下的 data/ 文件夹
# __file__ 是当前文件路径 (.../iGEM-silk/main/data_loader.py)
# .parents[1] 向上两级到 .../iGEM-silk/
DATA_DIR = Path(__file__).parents[1] / "data"


def load_fasta(path: str | Path) -> list[dict[str, str]]:
    """
    解析 FASTA 文件，返回统一格式的列表。

    参数
    ----
    path : FASTA 文件路径

    返回
    ----
    list[dict] : 每条序列一个 dict，包含：
        "id"       — 序列标识（> 后的第一个词，去除描述和管道符）
        "sequence" — 氨基酸序列（大写，拼接了所有序列行）

    解析细节
    --------
    - 以 ``>`` 开头的行是标识行。提取 ``>`` 后第一个空格或 ``|`` 之前的部分作为 id
    - 非标识行是序列行，可能跨多行（如每 60 个字符换行），自动拼接
    - 遇到新的标识行时，将上一条序列存入结果列表
    - 空行自动跳过
    - 单序列 FASTA（如 silk.fasta）也正常处理，返回单元素列表

    示例
    ----
    >>> entries = load_fasta("data/linker.fasta")
    >>> entries[0]
    {"id": "Flex_GGGGSx1", "sequence": "GGGGS"}
    """
    entries: list[dict[str, str]] = []
    current_id = ""
    current_seq: list[str] = []  # 用列表收集片段，最后 join，比字符串拼接高效

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue  # 跳过空行

            if line.startswith(">"):
                # ── 遇到新标识行 ──
                # 先把上一条序列（如果有）存入结果
                if current_seq:
                    entries.append({
                        "id": current_id,
                        "sequence": "".join(current_seq),
                    })
                # 提取 id：> 后的第一个词
                # ">Flex_GGGGSx1 | 最短柔性" → "Flex_GGGGSx1"
                # ">sp|P12345|PROT_HUMAN"  → "sp"
                current_id = line[1:].split(" ")[0].split("|")[0].strip()
                current_seq = []
            else:
                # ── 序列行：收集到列表 ──
                current_seq.append(line)

        # 处理最后一条序列（文件末尾没有 > 行来触发存储）
        if current_seq:
            entries.append({
                "id": current_id,
                "sequence": "".join(current_seq),
            })

    return entries


def load_scaffold(path: str | Path | None = None) -> dict[str, str]:
    """
    加载丝素蛋白骨架序列。

    默认从 ``data/silk.fasta`` 读取，只取第一条序列（一个项目只有一个 scaffold）。
    如果 FASTA 含多条序列，只返回第一条，其余忽略。

    返回 {"id": ..., "sequence": ...}。
    """
    if path is None:
        path = DATA_DIR / "silk.fasta"
    entries = load_fasta(path)
    if not entries:
        raise FileNotFoundError(f"No sequences found in {path}")
    return entries[0]


def load_linkers(path: str | Path | None = None) -> list[dict[str, str]]:
    """
    加载 linker 序列库。

    默认从 ``data/linker.fasta`` 读取。
    返回全部序列的列表，枚举阶段会自动追加一个空 linker（无 linker 选项）。

    linker 类型速览（来自 data/linker.fasta）：
      Flex_GGGGSx1/x2/x3   — 经典柔性 linker（Gly-Gly-Gly-Gly-Ser 重复）
      Rigid_EAAAKx1/x2     — 刚性 α-螺旋 linker（Glu-Ala-Ala-Ala-Lys）
      Helix_AEAAAKEAAAKA   — 带电荷的螺旋 linker
      Gly_rich_GPG         — 仿丝素弹性区 linker
      Pro_rich_PPP         — 富含脯氨酸的刚性转角
      PAS_linker           — 亲水非结构化 linker（抗非特异性吸附）
      Silk_like_GS         — 丝素衍生 linker（与骨架亲和）
    """
    if path is None:
        path = DATA_DIR / "linker.fasta"
    return load_fasta(path)


def load_function_peptides(path: str | Path | None = None) -> pd.DataFrame:
    """
    加载功能肽 CSV 数据库。

    默认从 ``data/function.csv`` 读取（约 2.5 万条肽序列）。
    返回 pandas DataFrame，关键列：

    - ``sequence``: 氨基酸序列
    - ``is_antioxidant``: 抗氧化标签 (0/1)
    - ``is_antimicrobial``: 抗菌标签 (0/1)
    - ``is_antiglycation``: 抗糖化标签 (0/1)
    - ``is_collagen_stimulating``: 促胶原标签 (0/1)
    - ``is_cell_penetrating``: 细胞穿透标签 (0/1)
    - ``source_name``: 肽来源（蛋白名+位置或 IN 编号）
    - ``database_id``: 数据库编号

    注：pandas 读取后自动 trim 列名首尾空格（原始 CSV 列名可能带空格）。
    """
    if path is None:
        path = DATA_DIR / "function.csv"
    df = pd.read_csv(path)
    # 标准化列名：去除可能存在的首尾空格
    df.columns = df.columns.str.strip()
    return df


def load_scaffold_sequence(scaffold_path: str | Path | None = None) -> str:
    """
    快捷方法：只返回 scaffold 的氨基酸序列字符串。

    等价于 ``load_scaffold(path)["sequence"]``。
    用于只需要序列、不关心 id 的场景。
    """
    return load_scaffold(scaffold_path)["sequence"]
