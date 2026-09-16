# 重组结构蛋白专利数据包 — 数据库导入交接说明

> 本包用于把 `patent_sequence_reorganized_2026-08-05/` 的专利序列数据导入关系型数据库。
> 目标库：**PostgreSQL**（其他库的适配见文末）。

---

## 一、包内容清单

```
patent_db_handoff/
├── README_导入说明.md                 ← 本文件
├── patent_db_schema.sql              ← 建表 DDL（含 DROP TABLE IF EXISTS，可重跑）
├── group_record_mapping.csv          ← 27 条记录 ↔ 11 个实验组的映射（含无 FASTA 行的合成主键）
├── import_patent_db.py               ← 一键导入脚本（读 TSV + FASTA，落库）
└── source_data/                      ← 原始数据包（原样复制，不要改动）
    ├── README.md
    ├── scaffold_patents_sequence_guide.tsv        ← 主表（27 行）
    ├── scaffold_patents_experiment_details.tsv    ← 实验明细表（12 行）
    ├── scaffold_patents_evidence_workbook.xlsx    ← 人工阅读版（与 TSV 同源）
    └── fasta_downloads/                           ← 16 个蛋白质 FASTA 文件
```

---

## 二、前置依赖

1. 一个空的（或可接受被清空的）PostgreSQL 数据库。
2. Python 3.8+，安装驱动：
   ```bash
   pip install psycopg2-binary      # 或 pip install psycopg（psycopg3）
   ```

---

## 三、导入步骤（一条命令）

```bash
python import_patent_db.py \
    --db "postgresql://用户名:密码@主机:5432/数据库名" \
    --apply-schema
```

- 不带 `--apply-schema` 时，脚本**只导数据**，假定表已存在（可先手动跑 `patent_db_schema.sql`）。
- 带 `--apply-schema` 时，脚本**先建表再导数据**（会 DROP 已存在的同名表，注意备份）。
- 也可通过环境变量传连接串：`export DATABASE_URL="postgresql://..."`，然后直接 `python import_patent_db.py --apply-schema`。
- 所有路径参数都有默认值，指向本包相对位置，所以在本包根目录直接运行即可。

脚本结束会打印**校验结果**，请确认与下方“预期”一致再交付。

---

## 四、数据库设计（建几张表？）

**结论：两张核心表 + 一张可选的 groups 维度表。**

| 表 | 行数 | 粒度 | 说明 |
|---|---|---|---|
| `patent_records` | 27 | 1 行 = 1 个序列记录 或 1 个无序列的专利/产品记录 | 主信息；含 `aa_sequence` 文本列 |
| `experiments` | 12 | 1 行 = 1 个 (实验组, 证据等级) 条目 | 实验明细；同一组可有多行(E1/E2/E4) |
| `groups`（可选） | 11 | 1 行 = 1 个实验组 | 维度表，作为 `patent_records` 与 `experiments` 的连接枢纽 |

**为什么拆两张而不是一张？** 两份 TSV 粒度不同：一个实验组（如 “Jinbo HC8/HC16 and 16-repeat constructs”）对应 **5 条序列记录 + 2 条实验行**。合并成一张表要么重复序列信息、要么丢掉 E1/E2/E3/E4 逐层实验细节。拆表是规范做法。

两表通过 `group_key`（短 slug，如 `jinbo_constructs`、`eadf4_c16`）关联。

---

## 五、必须知道的 4 个坑（已在新脚本/映射里处理，了解即可）

1. **11 条无 FASTA 记录的 `Sequence record ID` 是空的** → 它们没有天然主键。脚本对这 11 行按 `(申请人, 专利族)` 在 `group_record_mapping.csv` 里取**合成主键**（如 `BLOOMAGE_CN114805551A`），并分配 `group_key`。匹配做了归一化（去逗号、压缩空格、转小写）以容错。
2. **Yusong 的 2 条记录（有 FASTA，E2）不在 12 个实验组中** → 其 `group_key` 置 `NULL`（映射表里标记为 `unmapped`），不参与组关联。
3. **2 个实验组没有对应序列记录**：`jinbo_devices`（已上市医疗器械）、`trautec_kefuyan`（成品 Kefuyan）。它们只出现在 `experiments` / `groups` 表，用于组名→组键映射。
4. **`FHLC_SEQ_ID_NO_1.fasta` 的 FASTA 头部带管道符元数据**（`>FHLC_SEQ_ID_NO_1|protein=...|length=...`），前缀 `FHLC_SEQ_ID_NO_1` 正确，脚本按 `>` 后第一段取 ID，无需特殊处理。

---

## 六、导入后预期校验结果

```
patent_records 总行数 : 27  (期望 27)
  其中含 aa_sequence  : 16  (期望 16，即 16 个有 FASTA 的记录)
  其中 group_key 为 NULL: 2  (期望 2，即 Yusong)
experiments 总行数    : 12  (期望 12)
groups 总行数         : 11  (期望 11)
外键悬空(records.group_key 无对应 groups): 0  (期望 0)
```

---

## 七、适配其他数据库（非 PostgreSQL）

- **MySQL**：把 `patent_db_schema.sql` 里的 `BIGSERIAL` 改为 `BIGINT AUTO_INCREMENT`，`TEXT` 保持，`GENERATED ALWAYS AS IDENTITY` 删除；脚本里 `%s` 占位符对 MySQL 同样适用（psycopg 换成 `pymysql` 时占位符一致）。
- **SQLite**：`INTEGER PRIMARY KEY AUTOINCREMENT`；`%s` 占位符一致；`CHECK` 约束 SQLite 默认忽略（不影响导入）。`import_patent_db.py` 当前仅接 PostgreSQL 驱动，换库需改 `get_conn` 的 import。

---

## 八、故障排查

- `缺少 PostgreSQL 驱动` → 见第二节安装 psycopg2-binary。
- `实验组名未在映射表找到 group_key` → 检查 `source_data/scaffold_patents_experiment_details.tsv` 的 “Patent / product group” 是否与 `group_record_mapping.csv` 的 `group_name` 完全一致（应已对齐）。
- `无 FASTA 记录未能在映射表精确匹配` → 说明某条无序列记录的申请人/专利族与映射表不符，按提示的 (applicant, patent) 在 CSV 里补一行即可。
- 想重跑：直接再执行一次带 `--apply-schema` 的命令（会先 DROP 再建）。
