# 重组结构蛋白专利数据包 / Recombinant Structural Protein Patent Data Pack

本文件夹整理了重组胶原蛋白、丝蛋白、弹性蛋白及相关结构蛋白专利的序列、产品、监管状态和实验验证信息。

This folder summarizes sequence, product, regulatory-status, and experimental-evidence information for patents involving recombinant collagen, silk, elastin, and related structural proteins.

## 文件结构 / File Structure

- `scaffold_patents_sequence_master.tsv`  
  主信息表。每行对应一条 FASTA 序列或一个无法获得唯一序列的专利/产品记录。第一列为 FASTA 文件名，并包括专利、产品用途、市场批号、实验概述、证据等级及 PDB/CIF accession。  
  Main information table. Each row represents a FASTA sequence or a patent/product for which no unique sequence was available. The first column is the FASTA filename. The table also includes patents, products, regulatory identifiers, experiments, evidence levels, and PDB/CIF accessions.

- `scaffold_patents_experiment_details.tsv`  
  实验明细表，说明具体进行了什么实验、主要结果、结果在专利或外部资料中的位置，以及证据局限。  
  Experiment-detail table describing the tests performed, principal findings, where the results can be found, and important limitations.

- `scaffold_patents_evidence_workbook.xlsx`  
  便于人工阅读的 Excel 版本，包含“序列主表”“实验与结果位置”“分级与使用说明”三个工作表。  
  Human-readable Excel workbook containing three sheets: sequence master table, experiment/result locations, and evidence-level guidance.

- `fasta_downloads/`  
  按记录拆分的蛋白质 FASTA 文件。没有可靠唯一序列、属于复杂混合物或不适合分发的记录不会生成 FASTA。  
  Individually separated protein FASTA files. No FASTA is generated for records lacking a reliable unique sequence, representing complex mixtures, or unsuitable for distribution.

- `preview_*.png`  
  Excel 工作表的排版预览图，仅用于质量检查。  
  Layout previews of the Excel sheets, used only for quality assurance.

## 实验证据等级 / Experimental Evidence Levels

- **E1：纯化蛋白、理化或无细胞实验**  
  例如纯度、质谱、圆二色谱、热稳定性、自组装、材料力学或其他不使用活细胞的实验。  
  **Purified-protein, physicochemical, or cell-free experiments**, such as purity, mass spectrometry, circular dichroism, thermal stability, self-assembly, or material testing.

- **E2：细胞实验**  
  在培养细胞中进行的安全性或功能性评价，例如细胞毒性、黏附、增殖、迁移和基因表达。  
  **Cell-based experiments**, such as cytotoxicity, adhesion, proliferation, migration, or gene-expression assays.

- **E3：动物实验**  
  在动物模型中进行的毒理、安全性、组织修复、降解或功效研究。  
  **Animal experiments**, including toxicology, safety, tissue repair, degradation, or efficacy studies in animal models.

- **E4：人体使用或对照临床研究**  
  包括人体使用测试、受试者研究或有对照的临床研究，但不一定已经完成监管批准。  
  **Human use or controlled clinical studies**, including volunteer-use studies or controlled trials, without necessarily implying regulatory approval.

- **E5：监管审评、受控临床和上市后安全数据**  
  产品已经进入正式监管审评或获批，并具有注册临床、监管技术审查或上市后安全监测信息。  
  **Regulatory review, controlled clinical evidence, and post-market safety data**, indicating formal regulatory evaluation or approval and associated clinical or post-market evidence.

表中的标签表示目前能够核实到的最高证据等级，不表示已经完成所有较低或较高等级的实验。专利中的用途或效果声明也不自动等同于独立验证结果。

The assigned label represents the highest evidence level that could be verified. It does not mean that every lower or higher evidence stage has been completed. Patent claims should not automatically be treated as independently validated results.

监管信息核查日期：**2026-08-05**。“未核实到”不等于证明相关记录不存在。

Regulatory information checked through **2026-08-05**. “Not verified” does not prove that no corresponding record exists.
