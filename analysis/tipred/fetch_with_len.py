import psycopg2, numpy as np, time
c = psycopg2.connect('host=127.0.0.1 dbname=igem_peptides user=igem password=igem_local_2026')
c.set_session(readonly=True)
cur = c.cursor()
cur.execute("SET max_parallel_workers_per_gather = 1")
cur.execute("SET work_mem = '64MB'")

t0 = time.time()
cur.execute("""
DECLARE tip_len_cur CURSOR FOR
SELECT pe.score, length(p.sequence) AS plen, pe.label
FROM peptide_enrichment pe JOIN peptides p ON p.id = pe.peptide_id
WHERE pe.tool='tipred' AND pe.score IS NOT NULL
  AND pe.peptide_id % 102 = 0
LIMIT 300000
""")
print(f'declare: {time.time()-t0:.1f}s')
score, label, length = [], [], []
last_log = time.time()
while True:
    cur.execute("FETCH FORWARD 2000 FROM tip_len_cur")
    rows = cur.fetchall()
    if not rows: break
    for r in rows:
        score.append(r[0])
        length.append(r[1])
        label.append(r[2])
    if time.time() - last_log > 10:
        print(f'  {len(score)}, {time.time()-t0:.1f}s')
        last_log = time.time()
cur.execute("CLOSE tip_len_cur")
print(f'fetched: {len(score)}, {time.time()-t0:.1f}s')

arr_score  = np.array(score, dtype=np.float32)
arr_label  = np.array(label, dtype=object)
arr_length = np.array(length, dtype=np.int16)
np.savez_compressed('/home/lenovo/Projects/iGEM-platform/results/plots/tipred/tipred_with_length.npz',
                    score=arr_score, label=arr_label, length=arr_length)
print(f'saved, n={len(arr_score)}')
import collections
print('label distribution:', collections.Counter(arr_label))