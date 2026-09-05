"""bp3_esm_only.py — 只测 ESM-2 编码本身的速度

绕开 bp3 库所有 DenseNet / 文件IO / acc_id 索引,只看 ESM-2 650M
在 RTX 5880 Ada 上能做到多少 seq/s。这是理论上限。
"""
import os
os.environ.setdefault("TORCH_HOME",
    "/home/lenovo/Projects/iGEM-silk/tools/models/fair-esm")
import sys
sys.path.insert(0, "/home/lenovo/Projects/iGEM-silk")

import time
import torch
import esm


def main():
    print("loading ESM-2 ...")
    t0 = time.perf_counter()
    model, alphabet = esm.pretrained.esm2_t33_650M_UR50D()
    model = model.eval().cuda()
    batch_converter = alphabet.get_batch_converter()
    print(f"loaded in {time.perf_counter()-t0:.1f}s, GPU mem {torch.cuda.memory_allocated()/1e9:.2f}GB")

    # 加载测试肽
    seqs = []
    with open("/tmp/bp3_test_5000.fasta") as f:
        for line in f:
            s = line.strip()
            if s and not s.startswith(">"):
                seqs.append(s.upper())

    # 测多种 batch size
    print(f"\n{'batch':>6} {'batches':>9} {'seqs':>6} {'time':>7} {'seq/s':>7} {'GPU util':>8}")
    for batch_size in [50, 100, 200, 500, 1000]:
        n_batches = 4 if batch_size <= 200 else 2
        # warmup
        data = [(str(i), seqs[i % len(seqs)]) for i in range(batch_size)]
        _, _, tokens = batch_converter(data)
        tokens = tokens.cuda()
        with torch.no_grad():
            model(tokens, repr_layers=[33])
        torch.cuda.synchronize()

        latencies = []
        total_seqs = 0
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        for b in range(n_batches):
            data = [(str(i), seqs[(b*batch_size+i) % len(seqs)]) for i in range(batch_size)]
            _, _, tokens = batch_converter(data)
            tokens = tokens.cuda()
            t1 = time.perf_counter()
            with torch.no_grad():
                results = model(tokens, repr_layers=[33])
            torch.cuda.synchronize()
            latencies.append(time.perf_counter() - t1)
            total_seqs += batch_size
        torch.cuda.synchronize()
        elapsed = time.perf_counter() - t0
        thr = total_seqs / elapsed
        print(f"{batch_size:>6} {n_batches:>9} {total_seqs:>6} {elapsed:>6.2f}s {thr:>7.1f} seq/s")


if __name__ == "__main__":
    main()