-- 16_antimelanin_self_check.sql
-- 抗黑素预计算库落库后的自检，验证 pipeline §1–§5 落库正确。
-- 与抗菌/抗氧化方向的区别：所有 passed construct 标 status='WIP'。
--
-- 用法：psql -f src/setup/sql/16_antimelanin_self_check.sql

\echo === 1. 总数 + 通道分布（WIP 是该方向特有的合法 status） ===
SELECT channel, status, count(*) AS n
  FROM constructs
 WHERE direction='antimelanin'
 GROUP BY channel, status
 ORDER BY channel, status;

\echo === 2. Top 通道 composite_topical 必须非空 + 降序排列 ===
SELECT min((delivery_scores->>'topical')::float8)    AS min_topical,
       max((delivery_scores->>'topical')::float8)    AS max_topical,
       min((delivery_scores->>'injection')::float8)  AS min_injection,
       max((delivery_scores->>'injection')::float8)  AS max_injection
  FROM constructs
 WHERE direction='antimelanin' AND channel='top';

\echo === 3. failed_safety 不应出现（pipeline 只落 passed→WIP） ===
SELECT count(*) FROM constructs
 WHERE direction='antimelanin' AND status='failed_safety';

\echo === 4. 11 项分数 + func_score_meaning 必须全部存在 ===
SELECT count(*) FILTER (WHERE scores ? 'tipred')                   AS n_tipred,
       count(*) FILTER (WHERE scores ? 'func_score_meaning')       AS n_func_meaning,
       count(*) FILTER (WHERE scores ? 'toxinpred3')               AS n_tox,
       count(*) FILTER (WHERE scores ? 'hemopi2')                  AS n_hemo,
       count(*) FILTER (WHERE scores ? 'mhcflurry')                AS n_mhci,
       count(*) FILTER (WHERE scores ? 'netmhcipan_min_rank_pct')  AS n_mhcii_rank,
       count(*) FILTER (WHERE scores ? 'netmhcipan_has_sb')        AS n_mhcii_has_sb,
       count(*) FILTER (WHERE scores ? 'plm4cpps')                 AS n_cpp,
       count(*) FILTER (WHERE scores ? 'algpred2')                 AS n_sens,
       count(*) FILTER (WHERE scores ? 'bepipred3')                AS n_bepi,
       count(*) FILTER (WHERE scores ? 'aggrescan_a3v')            AS n_agg
  FROM constructs
 WHERE direction='antimelanin';

\echo === 5. func_score_meaning 必须是 ranking_only_not_probability（提案 §5） ===
SELECT scores->>'func_score_meaning' AS func_score_meaning, count(*) AS n
  FROM constructs
 WHERE direction='antimelanin'
 GROUP BY scores->>'func_score_meaning'
 ORDER BY n DESC;

\echo === 6. Bottom 通道的 tipred 必须显著低于 Top（按提案退化排序逻辑） ===
SELECT channel,
       percentile_disc(0.5) WITHIN GROUP (ORDER BY (scores->>'tipred')::float8) AS median_tipred,
       min((scores->>'tipred')::float8) AS min_tipred,
       max((scores->>'tipred')::float8) AS max_tipred
  FROM constructs
 WHERE direction='antimelanin'
 GROUP BY channel
 ORDER BY channel;

\echo === 7. 覆盖度（评估/未评估 标记数） ===
SELECT count(*) FILTER (WHERE assessed_hemo)  AS n_assessed_hemo,
       count(*) FILTER (WHERE assessed_mhci)  AS n_assessed_mhci,
       count(*) FILTER (WHERE assessed_mhcii) AS n_assessed_mhcii
  FROM constructs
 WHERE direction='antimelanin';

\echo === 8. Top 通道 tipred 应在 P99 附近（验证排序逻辑正确） ===
SELECT count(*) FILTER (WHERE (scores->>'tipred')::float8 >= 0.95) AS n_top_p99,
       count(*) FILTER (WHERE (scores->>'tipred')::float8 <= 0.10) AS n_bot_p10
  FROM constructs
 WHERE direction='antimelanin';

\echo === 9. status 列取值（确认 WIP 是合法值） ===
SELECT DISTINCT status FROM constructs WHERE direction='antimelanin' ORDER BY status;