-- 17_antiinflammatory_self_check.sql
-- 抗炎预计算库落库后的自检，验证 pipeline §1–§7 落库正确（WIP-specific）。
--
-- 用法：psql -h 127.0.0.1 -U igem -d igem_peptides -f src/setup/sql/17_antiinflammatory_self_check.sql

\echo === 1. 总数 + 通道分布（WIP 是该方向特有的合法 status） ===
SELECT channel, status, count(*) AS n
  FROM constructs
 WHERE direction='anti_inflammatory'
 GROUP BY channel, status
 ORDER BY channel, status;

\echo === 2. Top 通道 composite_topical 必须非空 + 降序排列 ===
SELECT min((delivery_scores->>'topical')::float8)    AS min_topical,
       max((delivery_scores->>'topical')::float8)    AS max_topical,
       min((delivery_scores->>'injection')::float8)  AS min_injection,
       max((delivery_scores->>'injection')::float8)  AS max_injection
  FROM constructs
 WHERE direction='anti_inflammatory' AND channel='top';

\echo === 3. failed_safety 不应出现（pipeline 只落 passed→WIP） ===
SELECT count(*) FROM constructs
 WHERE direction='anti_inflammatory' AND status='failed_safety';

\echo === 4. 14 项分数 + func_score_meaning 必须全部存在 ===
SELECT count(*) FILTER (WHERE scores ? 'aip_imfp')                       AS n_aip_imfp,
       count(*) FILTER (WHERE scores ? 'aip_specific')                   AS n_aip_specific,
       count(*) FILTER (WHERE scores ? 'other_max')                      AS n_other_max,
       count(*) FILTER (WHERE scores ? 'imfp_lg_acp')                    AS n_imfp_acp,
       count(*) FILTER (WHERE scores ? 'imfp_lg_adp')                    AS n_imfp_adp,
       count(*) FILTER (WHERE scores ? 'imfp_lg_ahp')                    AS n_imfp_ahp,
       count(*) FILTER (WHERE scores ? 'imfp_lg_amp')                    AS n_imfp_amp,
       count(*) FILTER (WHERE scores ? 'aopxsvm')                        AS n_aopxsvm,
       count(*) FILTER (WHERE scores ? 'amp_esm')                        AS n_amp_esm,
       count(*) FILTER (WHERE scores ? 'toxinpred3')                     AS n_tox,
       count(*) FILTER (WHERE scores ? 'hemopi2')                        AS n_hemo,
       count(*) FILTER (WHERE scores ? 'mhcflurry')                      AS n_mhci,
       count(*) FILTER (WHERE scores ? 'netmhcipan_min_rank_pct')        AS n_mhcii_rank,
       count(*) FILTER (WHERE scores ? 'netmhcipan_has_sb')              AS n_mhcii_has_sb,
       count(*) FILTER (WHERE scores ? 'plm4cpps')                       AS n_cpp,
       count(*) FILTER (WHERE scores ? 'algpred2')                       AS n_sens,
       count(*) FILTER (WHERE scores ? 'bepipred3')                      AS n_bepi,
       count(*) FILTER (WHERE scores ? 'aggrescan_a3v')                  AS n_agg,
       count(*) FILTER (WHERE scores ? 'func_score_meaning')             AS n_func_meaning
  FROM constructs
 WHERE direction='anti_inflammatory';

\echo === 5. func_score_meaning 必须是 ranking_only_not_probability（提案 §7） ===
SELECT scores->>'func_score_meaning' AS func_score_meaning, count(*) AS n
  FROM constructs
 WHERE direction='anti_inflammatory'
 GROUP BY scores->>'func_score_meaning'
 ORDER BY n DESC;

\echo === 6. Bottom 通道的 aip_imfp 必须显著低于 Top（提案 §3 退化排序逻辑） ===
SELECT channel,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (scores->>'aip_imfp')::float8) AS median_aip_imfp,
       min((scores->>'aip_imfp')::float8) AS min_aip_imfp,
       max((scores->>'aip_imfp')::float8) AS max_aip_imfp
  FROM constructs
 WHERE direction='anti_inflammatory'
 GROUP BY channel
 ORDER BY channel;

\echo === 7. 跨功能去混淆效果验证（aip_specific 应大于 aip_imfp 的纯 rank pct，提案 §4.2） ===
SELECT channel,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (scores->>'aip_specific')::float8) AS median_aip_specific,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (scores->>'other_max')::float8)    AS median_other_max
  FROM constructs
 WHERE direction='anti_inflammatory'
 GROUP BY channel
 ORDER BY channel;

\echo === 8. 覆盖度（评估/未评估 标记数） ===
SELECT count(*) FILTER (WHERE assessed_hemo)  AS n_assessed_hemo,
       count(*) FILTER (WHERE assessed_mhci)  AS n_assessed_mhci,
       count(*) FILTER (WHERE assessed_mhcii) AS n_assessed_mhcii
  FROM constructs
 WHERE direction='anti_inflammatory';

\echo === 9. status 列取值（确认 WIP 是合法值） ===
SELECT DISTINCT status FROM constructs WHERE direction='anti_inflammatory' ORDER BY status;

\echo === 10. 长度分布（验证硬约束 [11,30] 生效） ===
SELECT p.length, count(*) AS n
  FROM constructs c JOIN peptides p ON p.id = c.peptide_id
 WHERE c.direction='anti_inflammatory'
 GROUP BY p.length
 ORDER BY p.length;
