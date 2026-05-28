### ToxinPred3 (Yuecheng)

> Rathore et al., 2024, “ToxinPred3.0: An improved method for predicting the toxicity of peptides,” Computers in Biology and Medicine, 179:108926

**hard filter：`toxicity_score >= 0.38` 的候选先降级或剔除**

Model 1 是 Extra Trees classifier，使用 AAC + DPC，也就是 20 个 amino acid composition + 400 个 dipeptide composition；

Model 2 是 hybrid model = Extra Trees + MERCI motif module，把机器学习分数和 motif 分数合成 hybrid score。

### HemoPI2 (Yuecheng)

> Rathore et al., 2025, “Prediction of hemolytic peptides and their hemolytic concentration,” Communications Biology

专门预测 peptide 是否会裂解 mammalian RBCs，并且比旧工具多了一个定量任务：预测 **HC50**

对功能肽、linker junction、fusion construct sliding windows 做 HemoPI2 scan。

**HC50 ≤ 100 μM = hemolytic; HC50 > 100 μM = non-hemolytic**

若预测 **`HC50<=100µM`**或 **hemolytic score** 高，应该直接降级。尤其是 CPP、AMP-like peptide、疏水/带正电片段，很容易被它打高分。

### AlgPred2（Wenxuan）

> Sharma, N., Patiyal, S., Dhall, A., Pande, A., Arora, C., & Raghava, G. P. S. (2021). AlgPred 2.0: An improved method for predicting allergenic proteins and mapping of IgE epitopes. *Briefings in Bioinformatics*, *22*(4), bbaa294. https://doi.org/10.1093/bib/bbaa294

- **score > 0.3**：更可能是 allergenic。
- **score <= 0.3**：更可能是 non-allergenic

### TemStaPro [binary, not score] (Yuecheng)

> Pudžiuvelytė et al., 2024, “TemStaPro: protein thermostability prediction using sequence representations from protein language models,” Bioinformatics, 40(4), btae157

预测protein sequence 是否在多个温度阈值以上仍可能稳定。

核心 index 可以理解为：

```
stable_above_40C: yes/no
stable_above_45C: yes/no
...
stable_above_65C: yes/no
highest non-conflicting stable interval
```

如果 40°C 判不稳定、50°C 判稳定，这种结果是 conflict，TemStaPro 会标记 prediction clash。作者建议只有无冲突结果才更适合解释为“最高稳定温度区间”。

