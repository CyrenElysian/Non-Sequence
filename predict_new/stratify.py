"""线性 vs 非线性脚本的规模对照实验（取代旧 predict/stratify.py）。

目的
----
论文的核心结论之一是"非线性脚本明显比线性脚本难"，但两组图规模不同
（线性平均 6.68 节点 / 5.68 边，非线性 7.45 / 7.50），而 EM 要求整张图全对，
图越大需要同时对的决策就越多。本脚本用三类对照把"图规模"分离出去：

  1. 描述统计：两组节点数 / 边数 / 深度分布
  2. 按节点数分层：每层内部比较；样本过少的相邻层自动合并
  3. 精确节点数配对：为每条非线性样本配一条节点数完全相同的线性样本，
     做配对置换检验；并给出"控制规模前后差距变化"
  4. 回归控制：以 joint EM 为因变量，控制节点数 / 边数 / 嵌套深度

实现说明
--------
**完全不依赖 numpy**（本项目 non_seq 环境的 numpy 2.5.3 BLAS/LAPACK 链接损坏，
任何矩阵乘法或 np.linalg 调用都会触发无法捕获的原生崩溃 0xc06d007f）。
随机化检验用标准库 random，回归用纯 Python 高斯消元 + 正规方程。

用法
----
    python stratify.py --results run_x.results.json
    python stratify.py --results run_x.results.json --out stratify_x.md \
        --bin-edges 0 5 6 7 8 99 --min-cell 15 --permutations 10000
"""

from __future__ import annotations

import argparse
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cslib import dataset, metrics, structures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GROUND_TRUTH = dataset.DEFAULT_GROUND_TRUTH
DEFAULT_BIN_EDGES = [0, 5, 6, 7, 8, 99]
DEFAULT_MIN_CELL = 15


def resolve(path, base=HERE):
    return path if os.path.isabs(path) else os.path.join(base, path)


# ---------------------------------------------------------------- 载入

def load_joined(ground_truth_path, results_path):
    """把金标与预测按 id 合并，并**从原始边集重算**所有指标（与结果文件版本无关）。"""
    gold_records, reference_graphs, reference_stats, gold_meta = dataset.load_gold(ground_truth_path)
    gold_by_id = {it["id"]: it for it in gold_records}

    records = dataset.load_records(results_path)
    rows = []
    for r in records:
        rid = r.get("id")
        if rid not in gold_by_id:
            continue
        item = gold_by_id[rid]
        gold = reference_graphs[rid]
        gold_edges = gold["edges"]
        m = metrics.compute_edge_metrics(r.get("edges"), gold_edges)
        _, sg_match = metrics.compare_graphs(r.get("edges"), r.get("script_graph"),
                                             gold_edges, gold["script_graph"])
        stats = reference_stats[rid]
        rows.append({
            "id": rid,
            "linearity": structures.nonlinearity_label(stats["type_cnt"]),
            "type_cnt": stats["type_cnt"],
            "n_nodes": len(item["unordered_nodes"]),
            "n_edges": len(set(gold_edges)),
            "max_depth": int(stats["max_depth"] or 0),
            "em": (m["ged"] == 0) and sg_match,
            "em_edges": m["ged"] == 0,
            "f1": m["f1"],
            "iou": m["iou"],
            "nged": m["nged"],
            "ged": m["ged"],
            "failed": bool(r.get("error")),
        })
    return rows, gold_meta


# ---------------------------------------------------------------- 统计工具

def mean(vals):
    return sum(vals) / len(vals) if vals else 0.0


def mean_of(rows, key):
    return mean([r[key] for r in rows])


def wilson_ci(rows, key="em"):
    k = sum(1 for r in rows if r[key])
    return metrics.wilson_interval(k, len(rows))


def bucket_of(n, edges):
    lo = None
    for hi in edges:
        if n <= hi:
            return f"<={hi}" if lo is None else f"{lo}-{hi}"
        lo = hi + 1
    return f">={edges[-1] + 1}"


def merge_small_cells(rows, min_cell):
    """把样本不足的相邻区间合并，直到每组都达标或无可合并。"""
    merged = list(rows)
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, (label, lin, non) in enumerate(merged):
            if len(lin) < min_cell or len(non) < min_cell:
                j = i - 1 if i > 0 else i + 1
                a, b = min(i, j), max(i, j)
                la, lina, nona = merged[a]
                lb, linb, nonb = merged[b]
                merged[a] = (f"{la} ∪ {lb}", lina + linb, nona + nonb)
                del merged[b]
                changed = True
                break
    return merged


def paired_permutation(non_vals, lin_vals, n_perm, seed):
    """配对置换检验：每对内随机交换标签，得到差值零分布。返回 (线性−非线性, p)。"""
    d = [l - n for l, n in zip(lin_vals, non_vals)]
    if not d:
        return 0.0, float("nan")
    obs = mean(d)
    rng = random.Random(seed)
    hits = 0
    n = len(d)
    for _ in range(n_perm):
        s = 0.0
        for v in d:
            s += v if rng.getrandbits(1) else -v
        if abs(s / n) >= abs(obs) - 1e-12:
            hits += 1
    return obs, hits / n_perm


def bootstrap_ci(vals, n_boot, seed, alpha=0.05):
    if not vals:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    n = len(vals)
    means = []
    for _ in range(n_boot):
        means.append(sum(vals[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(alpha / 2 * n_boot)], means[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]


# ---------------------------------------------------------------- 回归（纯 Python）

def gauss_solve(A, b):
    A = [[float(v) for v in row] for row in A]
    b = [float(v) for v in b]
    n = len(A)
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            raise ValueError("设计矩阵奇异")
        if piv != col:
            A[col], A[piv] = A[piv], A[col]
            b[col], b[piv] = b[piv], b[col]
        for row in range(col + 1, n):
            factor = A[row][col] / A[col][col]
            if factor == 0.0:
                continue
            for c in range(col, n):
                A[row][c] -= factor * A[col][c]
            b[row] -= factor * b[col]
    x = [0.0] * n
    for row in range(n - 1, -1, -1):
        acc = sum(A[row][c] * x[c] for c in range(row + 1, n))
        x[row] = (b[row] - acc) / A[row][row]
    return x


def ols(y, X, names, out):
    y = [float(v) for v in y]
    X = [[float(v) for v in row] for row in X]
    n, k = len(X), len(X[0])
    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]
    beta = gauss_solve(XtX, Xty)
    resid = [y[i] - sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    dof = max(n - k, 1)
    sigma2 = sum(r * r for r in resid) / dof
    se = []
    for j in range(k):
        e = [0.0] * k
        e[j] = 1.0
        z = gauss_solve(XtX, e)
        se.append((sigma2 * z[j]) ** 0.5 if sigma2 * z[j] > 0 else 0.0)
    ybar = mean(y)
    tss = sum((v - ybar) ** 2 for v in y)
    r2 = 1 - sum(r * r for r in resid) / tss if tss > 0 else float("nan")
    out(f"  因变量均值 = {ybar:.4f}，n = {n}，R² = {r2:.4f}")
    out("  | 变量 | 系数 | 标准误 | t |")
    out("  |---|---|---|---|")
    for nm, b, s in zip(names, beta, se):
        out(f"  | {nm} | {b:+.4f} | {s:.4f} | {(b / s if s > 0 else float('nan')):+.2f} |")
    return beta, se


# ---------------------------------------------------------------- 报告

def build_report(rows, label, args, out):
    lin_all = [r for r in rows if r["linearity"] == "linear"]
    non_all = [r for r in rows if r["linearity"] == "nonlinear"]

    out(f"# 线性 vs 非线性分层对照 —— {label}\n")
    out(f"- 金标：`{args.ground_truth}`")
    out(f"- 预测：`{args.results}`")
    out(f"- 有效样本：{len(rows)} 条（线性 {len(lin_all)} / 非线性 {len(non_all)}）")
    failed = [r for r in rows if r["failed"]]
    if failed:
        out(f"- 失败样本：{len(failed)} 条（按空预测计入）")
    out(f"- 置换次数 {args.permutations}，自助次数 {args.bootstrap}，种子 {args.seed}\n")

    # 1. 规模
    out("## 1. 两组的图规模（先确认混淆因素存在）\n")
    out("| 组 | n | 节点数 均值[min,max] | 边数 均值[min,max] | 深度 均值 |")
    out("|---|---|---|---|---|")
    for name, grp in (("linear", lin_all), ("nonlinear", non_all)):
        if not grp:
            continue
        nn = [r["n_nodes"] for r in grp]
        ne = [r["n_edges"] for r in grp]
        nd = [r["max_depth"] for r in grp]
        out(f"| {name} | {len(grp)} | {mean(nn):.2f} [{min(nn)},{max(nn)}] | "
            f"{mean(ne):.2f} [{min(ne)},{max(ne)}] | {mean(nd):.2f} |")
    if lin_all and non_all:
        out(f"\n- 节点数差：{mean([r['n_nodes'] for r in non_all]) - mean([r['n_nodes'] for r in lin_all]):+.2f}")
        out(f"- 边数差：{mean([r['n_edges'] for r in non_all]) - mean([r['n_edges'] for r in lin_all]):+.2f}\n")

    # 2. 分层
    out("## 2. 按节点数分层：层内比较\n")
    groups = {}
    for r in rows:
        groups.setdefault(bucket_of(r["n_nodes"], args.bin_edges), {"linear": [], "nonlinear": []})
        groups[bucket_of(r["n_nodes"], args.bin_edges)][r["linearity"]].append(r)
    raw = [(k, v["linear"], v["nonlinear"]) for k, v in sorted(groups.items(), key=lambda kv: len(kv[0]))]

    out(f"原始分箱（--min-cell={args.min_cell}）：\n")
    out("| 节点数区间 | linear n | nonlinear n |")
    out("|---|---|---|")
    for lab, lin, non in raw:
        flag = " [样本不足]" if (len(lin) < args.min_cell or len(non) < args.min_cell) else ""
        out(f"| {lab} | {len(lin)}{flag} | {len(non)}{flag} |")
    out("")

    merged = merge_small_cells(raw, args.min_cell)
    out("合并后逐层比较（EM = joint exact match）：\n")
    out("| 节点数区间 | linear n | linear EM [95%CI] | nonlinear n | nonlinear EM [95%CI] | 差距(pp) |")
    out("|---|---|---|---|---|---|")
    for lab, lin, non in merged:
        if not lin or not non:
            continue
        ll, lh = wilson_ci(lin)
        nl, nh = wilson_ci(non)
        out(f"| {lab} | {len(lin)} | {mean_of(lin,'em')*100:.1f}% [{ll*100:.1f},{lh*100:.1f}] | "
            f"{len(non)} | {mean_of(non,'em')*100:.1f}% [{nl*100:.1f},{nh*100:.1f}] | "
            f"{(mean_of(lin,'em')-mean_of(non,'em'))*100:+.1f} |")
    out("")

    # 3. 精确配对
    out("## 3. 精确节点数配对（核心证据）\n")
    lin_by_n, non_by_n = {}, {}
    for r in rows:
        (lin_by_n if r["linearity"] == "linear" else non_by_n).setdefault(r["n_nodes"], []).append(r)
    rng = random.Random(args.seed)
    plin, pnon, detail, unmatched = [], [], [], 0
    for n in sorted(non_by_n):
        pool_l = sorted(lin_by_n.get(n, []), key=lambda r: r["id"])
        pool_n = sorted(non_by_n[n], key=lambda r: r["id"])
        take = min(len(pool_l), len(pool_n))
        rng.shuffle(pool_l)
        rng.shuffle(pool_n)
        plin += sorted(pool_l[:take], key=lambda r: r["id"])
        pnon += sorted(pool_n[:take], key=lambda r: r["id"])
        detail.append((n, len(pool_l), len(pool_n), take))
        unmatched += len(pool_n) - take

    out("| 节点数 | 可用 linear | 可用 nonlinear | 实际配对数 |")
    out("|---|---|---|---|")
    for n, a, b, t in detail:
        out(f"| {n} | {a} | {b} | {t} |")
    out(f"\n配对总数 **{len(plin)} 对**（覆盖 {len(plin)*2} 条）；"
        f"同规模线性样本不足而未能配对的非线性样本 **{unmatched}** 条\n")

    if plin:
        out("差值一律定义为 **linear − nonlinear**；EM/F1 为正表示线性更容易，"
            "NGED 为负表示非线性误差更大：\n")
        out("| 指标 | linear | nonlinear | 差值 | 置换检验 p |")
        out("|---|---|---|---|---|")
        for key, name in (("em", "Exact Match (joint)"), ("em_edges", "Exact Match (edges only)"),
                          ("f1", "F1"), ("iou", "IoU"), ("nged", "NGED (GED/|E|，越小越好)")):
            diff, p = paired_permutation([r[key] for r in pnon], [r[key] for r in plin],
                                         args.permutations, args.seed)
            out(f"| {name} | {mean_of(plin,key):.4f} | {mean_of(pnon,key):.4f} | "
                f"{diff:+.4f} | {p:.4g} |")
        lo, hi = bootstrap_ci([r["em"] for r in plin], args.bootstrap, args.seed)
        out(f"\nlinear EM 自助 95%CI: [{lo*100:.1f}%, {hi*100:.1f}%]")
        lo, hi = bootstrap_ci([r["em"] for r in pnon], args.bootstrap, args.seed)
        out(f"nonlinear EM 自助 95%CI: [{lo*100:.1f}%, {hi*100:.1f}%]\n")

        full_gap = mean_of(lin_all, "em") - mean_of(non_all, "em")
        pair_gap = mean_of(plin, "em") - mean_of(pnon, "em")
        out("**对照**：")
        out(f"- 全量（未控制规模）EM 差距：{full_gap*100:+.1f} pp")
        out(f"- 配对（节点数完全相同）EM 差距：{pair_gap*100:+.1f} pp")
        out(f"- 差距变化：{(pair_gap-full_gap)*100:+.1f} pp —— "
            f"{'控制规模后差距依然存在，不能只归因于图更大' if pair_gap > 0.10 else '控制规模后差距明显缩小，需谨慎解释'}\n")

    # 4. 回归
    out("## 4. 回归控制（线性概率模型，因变量 = joint EM）\n")
    out("> 说明：EM 是 0/1 变量，此处用线性概率模型 + 经典标准误；"
        "系数即「非线性相对线性的 EM 百分点差」。\n")
    y = [1.0 if r["em"] else 0.0 for r in rows]
    non = [1.0 if r["linearity"] == "nonlinear" else 0.0 for r in rows]
    nn = [float(r["n_nodes"]) for r in rows]
    ne = [float(r["n_edges"]) for r in rows]
    nd = [float(r["max_depth"]) for r in rows]
    one = [1.0] * len(rows)

    out("**模型 1**：EM ~ 1 + nonlinear")
    ols(y, [[o, n] for o, n in zip(one, non)], ["截距", "nonlinear"], out)
    out("\n**模型 2**：EM ~ 1 + nonlinear + 节点数")
    ols(y, [[o, n, a] for o, n, a in zip(one, non, nn)], ["截距", "nonlinear", "节点数"], out)
    out("\n**模型 3**：EM ~ 1 + nonlinear + 节点数 + 边数")
    ols(y, [[o, n, a, b] for o, n, a, b in zip(one, non, nn, ne)],
        ["截距", "nonlinear", "节点数", "边数"], out)
    out("\n**模型 4**：EM ~ 1 + nonlinear + 节点数 + 边数 + 嵌套深度")
    ols(y, [[o, n, a, b, c] for o, n, a, b, c in zip(one, non, nn, ne, nd)],
        ["截距", "nonlinear", "节点数", "边数", "嵌套深度"], out)
    out("")


def main(argv=None):
    p = argparse.ArgumentParser(description="线性 vs 非线性分层对照实验",
                                formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    p.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH,
                   help="标准数据集（含 type_cnt / max_depth）")
    p.add_argument("--results", required=True, help="run_generation.py 产出的 results 文件")
    p.add_argument("--label", default=None)
    p.add_argument("--out", default=None)
    p.add_argument("--bin-edges", type=int, nargs="+", default=DEFAULT_BIN_EDGES)
    p.add_argument("--min-cell", type=int, default=DEFAULT_MIN_CELL)
    p.add_argument("--permutations", type=int, default=10000)
    p.add_argument("--bootstrap", type=int, default=10000)
    p.add_argument("--seed", type=int, default=0)
    args = p.parse_args(argv)

    args.ground_truth = resolve(args.ground_truth)
    args.results = resolve(args.results)
    label = args.label or os.path.splitext(os.path.basename(args.results))[0]
    out_path = resolve(args.out) if args.out else os.path.join(HERE, f"stratify_{label}.md")

    rows, gold_meta = load_joined(args.ground_truth, args.results)
    if not rows:
        print("没有可用的配对样本。")
        return 1

    lines = []

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("gbk", errors="replace").decode("gbk"))
        lines.append(s)

    build_report(rows, label, args, out)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[已写入] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
