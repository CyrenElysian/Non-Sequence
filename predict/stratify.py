"""线性 vs 非线性脚本的分层对照实验。

背景
----
论文的一个核心结论是"非线性脚本明显比线性脚本难"。但审稿人指出：
两组样本的图规模不同（线性平均 6.68 节点 / 5.68 边，非线性 7.45 / 7.50），
而 Exact Match 要求整张图全对，图越大需要同时对的决策就越多，
所以"非线性更难"这一结论可能只是"图更大所以更难"。

本脚本用已有数据做三类对照，把"图规模"这个混淆因素分离出去：

  1. 描述统计：按线性/非线性给出节点数、边数、嵌套深度的分布与置信区间。
  2. 按节点数分层：在每个节点数区间内部分别比较两组的 EM / F1 / NGED。
     样本过少的区间按 --min-cell 合并，避免小格子产生无意义的"显著"。
  3. 精确节点数配对：为每条非线性样本配一条节点数完全相同的线性样本，
     在配对子集内比较，并做配对置换检验（permutation test）给出 p 值。
  4. 回归控制：以 EM 为因变量，控制节点数/边数/深度，估计"是否非线性"的偏效应。

用法
----
在本目录（predict/）下运行：

    python stratify.py
    python stratify.py --results results_v4-pro.json --out stratify_v4-pro.md
    python stratify.py --results a.json --results-label v4-flash --permutations 20000

不依赖 scipy / statsmodels：置换检验与 OLS 都用 numpy 实现。
"""

from __future__ import annotations

import argparse
import json
import math
import os
import random
from collections import defaultdict

import numpy as np

from metrics import (
    compute_edge_metrics,
    nonlinearity_label,
    summarize_group,
    _wilson_interval,
)

# ---------------------------------------------------------------- 配置

DEFAULT_GROUND_TRUTH = "../introduce/stats/CtrlScript_check_stats_v1.json"
DEFAULT_RESULTS = "results_v4-flash.json"
DEFAULT_BIN_EDGES = [0, 5, 6, 7, 8, 99]   # 区间上界；最后一档为 >=9
DEFAULT_MIN_CELL = 15                      # 每个区间每组至少多少条才单独报告


# ---------------------------------------------------------------- 工具

def load_records(ground_truth_path, results_path):
    """读入金标与预测，返回 (records, ref_info)，并为每条记录补齐规模字段。"""
    with open(ground_truth_path, "r", encoding="utf-8") as f:
        gold = json.load(f)
    with open(results_path, "r", encoding="utf-8") as f:
        preds = json.load(f)

    gold_by_id = {g["id"]: g for g in gold}
    ref_info = {}
    for g in gold:
        tc = g.get("type_cnt", {})
        ref_info[g["id"]] = {
            "type_cnt": tc,
            "max_depth": g.get("max_depth", 0),
            "linearity": nonlinearity_label(tc),
        }

    records = []
    for p in preds:
        rid = p.get("id")
        if rid not in gold_by_id:
            continue
        g = gold_by_id[rid]
        gold_edges = set(g.get("edges", []) or [])
        pred_edges = set(p.get("edges", []) or [])

        # 一律从原始边集重算，而不是读结果文件里的存量字段：
        # 旧结果文件没有 nged 字段，且 EM 口径在不同版本间有过变化。
        m = compute_edge_metrics(pred_edges, gold_edges)

        records.append({
            "id": rid,
            "linearity": ref_info[rid]["linearity"],
            "n_nodes": len(g.get("unordered_nodes", {})),
            "n_edges": len(gold_edges),
            "n_pred_edges": len(pred_edges),
            "max_depth": ref_info[rid]["max_depth"],
            "em": m["ged"] == 0 and bool(p.get("sg_match", False)),
            "em_edges": m["ged"] == 0,
            "sg_match": bool(p.get("sg_match", False)),
            "f1": m["f1"],
            "iou": m["iou"],
            "nged": m["nged"],
            "ged": m["ged"],
            "err": bool(p.get("error")),
        })
    return records, ref_info


def fmt_pct(x):
    return f"{x * 100:.1f}%"


def mean_of(records, key):
    return float(np.mean([r[key] for r in records])) if records else 0.0


def wilson_ci(records, key="em"):
    k = sum(1 for r in records if r[key])
    return _wilson_interval(k, len(records))


# ---------------------------------------------------------------- 1. 描述统计

def describe(records, out):
    lin = [r for r in records if r["linearity"] == "linear"]
    non = [r for r in records if r["linearity"] == "nonlinear"]

    out("## 1. 两组的基本规模（先确认混淆因素真实存在）\n")
    out("| 组 | n | 节点数 均值[min,max] | 边数 均值[min,max] | 深度 均值 |")
    out("|---|---|---|---|---|")
    for name, grp in (("linear", lin), ("nonlinear", non)):
        if not grp:
            continue
        nn = [r["n_nodes"] for r in grp]
        ne = [r["n_edges"] for r in grp]
        nd = [r["max_depth"] for r in grp]
        out(f"| {name} | {len(grp)} | {np.mean(nn):.2f} [{min(nn)},{max(nn)}] | "
            f"{np.mean(ne):.2f} [{min(ne)},{max(ne)}] | {np.mean(nd):.2f} |")
    if lin and non:
        out(f"\n- 节点数差：{np.mean([r['n_nodes'] for r in non]) - np.mean([r['n_nodes'] for r in lin]):+.2f}")
        out(f"- 边数差：{np.mean([r['n_edges'] for r in non]) - np.mean([r['n_edges'] for r in lin]):+.2f}")
    out("")


def bucket_of(n_nodes, bin_edges):
    """按节点数落入区间，返回可读标签。"""
    lo = None
    for hi in bin_edges:
        if n_nodes <= hi:
            if lo is None:
                return f"<={hi}"
            return f"{lo}-{hi}"
        lo = hi + 1
    return f">={bin_edges[-1] + 1}"


def merge_small_cells(rows, min_cell):
    """把相邻的、任一小组样本数不足 min_cell 的区间合并，直到满足或无可合并。"""
    merged = []
    for label, lin, non in rows:
        if merged and (len(lin) < min_cell or len(non) < min_cell):
            plabel, plin, pnon = merged[-1]
            merged[-1] = (f"{plabel} ∪ {label}", plin + lin, pnon + non)
        else:
            merged.append((label, lin, non))
    # 再从头收敛一次（合并后可能出现新的小格子）
    changed = True
    while changed and len(merged) > 1:
        changed = False
        for i, (label, lin, non) in enumerate(merged):
            if len(lin) < min_cell or len(non) < min_cell:
                j = i - 1 if i > 0 else i + 1
                a = min(i, j)
                b = max(i, j)
                la, lina, nona = merged[a]
                lb, linb, nonb = merged[b]
                merged[a] = (f"{la} ∪ {lb}", lina + linb, nona + nonb)
                del merged[b]
                changed = True
                break
    return merged


def stratified_table(records, bin_edges, min_cell, out):
    out("## 2. 按节点数分层：每层内部比较 linear vs nonlinear\n")
    groups = defaultdict(lambda: {"linear": [], "nonlinear": []})
    for r in records:
        groups[bucket_of(r["n_nodes"], bin_edges)][r["linearity"]].append(r)

    rows = []
    for label in sorted(groups, key=lambda s: (len(s), s)):
        rows.append((label, groups[label]["linear"], groups[label]["nonlinear"]))

    out(f"原始分箱（每格样本数，--min-cell={min_cell}）：\n")
    out("| 节点数区间 | linear n | nonlinear n |")
    out("|---|---|---|")
    for label, lin, non in rows:
        flag = " [样本不足]" if (len(lin) < min_cell or len(non) < min_cell) else ""
        out(f"| {label} | {len(lin)}{flag} | {len(non)}{flag} |")
    out("")

    rows = merge_small_cells(rows, min_cell)
    out("合并后逐层比较（EM = joint exact match）：\n")
    out("| 节点数区间 | linear n | linear EM [95%CI] | nonlinear n | nonlinear EM [95%CI] | 差距(pp) | linear F1 | nonlinear F1 |")
    out("|---|---|---|---|---|---|---|---|---|")
    for label, lin, non in rows:
        if not lin or not non:
            continue
        ll, lh = wilson_ci(lin)
        nl, nh = wilson_ci(non)
        lem, nem = mean_of(lin, "em"), mean_of(non, "em")
        out(f"| {label} | {len(lin)} | {fmt_pct(lem)} [{fmt_pct(ll)},{fmt_pct(lh)}] | "
            f"{len(non)} | {fmt_pct(nem)} [{fmt_pct(nl)},{fmt_pct(nh)}] | "
            f"{(lem - nem) * 100:+.1f} | {mean_of(lin,'f1'):.3f} | {mean_of(non,'f1'):.3f} |")
    out("")


# ---------------------------------------------------------------- 3. 精确配对

def build_pairs(records, seed=0):
    """为每条非线性样本配一条节点数完全相同的线性样本（贪心、可复现）。

    返回 (paired_nonlinear, paired_linear, 未能配对的非线性条数, 每档规模统计)
    """
    lin_by_n = defaultdict(list)
    non_by_n = defaultdict(list)
    for r in records:
        (lin_by_n if r["linearity"] == "linear" else non_by_n)[r["n_nodes"]].append(r)

    rng = random.Random(seed)
    paired_lin, paired_non = [], []
    detail = []
    unmatched = 0
    for n in sorted(non_by_n):
        pool_lin = sorted(lin_by_n.get(n, []), key=lambda r: r["id"])
        pool_non = sorted(non_by_n[n], key=lambda r: r["id"])
        take = min(len(pool_lin), len(pool_non))
        rng.shuffle(pool_lin)
        rng.shuffle(pool_non)
        paired_lin.extend(sorted(pool_lin[:take], key=lambda r: r["id"]))
        paired_non.extend(sorted(pool_non[:take], key=lambda r: r["id"]))
        detail.append((n, len(pool_lin), len(pool_non), take))
        unmatched += len(pool_non) - take
    return paired_non, paired_lin, unmatched, detail


def paired_permutation_test(non_vals, lin_vals, n_perm=10000, seed=0):
    """配对置换检验：在每一对内随机交换两组标签，得到差值的零分布。

    返回 (观测差值, 双尾 p 值)
    """
    d = np.asarray(non_vals, dtype=float) - np.asarray(lin_vals, dtype=float)
    observed = float(-d.mean())          # 定义为 linear - nonlinear
    rng = np.random.default_rng(seed)
    n = len(d)
    if n == 0:
        return 0.0, float("nan")
    signs = rng.choice([-1.0, 1.0], size=(n_perm, n))
    perm = (-(d * signs)).mean(axis=1)
    p = float((np.abs(perm) >= abs(observed) - 1e-12).mean())
    return observed, p


def bootstrap_ci(values, n_boot=10000, seed=0, alpha=0.05):
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return (float("nan"), float("nan"))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, arr.size, size=(n_boot, arr.size))
    means = arr[idx].mean(axis=1)
    return (float(np.quantile(means, alpha / 2)), float(np.quantile(means, 1 - alpha / 2)))


def matched_analysis(records, n_perm, seed, out):
    out("## 3. 精确节点数配对（核心证据）\n")
    non, lin, unmatched, detail = build_pairs(records, seed=seed)

    out("| 节点数 | 可用 linear | 可用 nonlinear | 实际配对数 |")
    out("|---|---|---|---|")
    for n, a, b, t in detail:
        out(f"| {n} | {a} | {b} | {t} |")
    total_pairs = len(lin)
    out(f"\n配对总数：**{total_pairs} 对**（覆盖 {total_pairs * 2} 条样本）；"
        f"因同规模线性样本不足而未能配对的非线性样本：**{unmatched}** 条\n")

    if total_pairs == 0:
        out("_无可用配对，跳过。_\n")
        return

    out("配对检验结果（差值一律定义为 **linear − nonlinear**，"
        "所以 EM / F1 为正表示线性更容易，NGED 为负表示非线性误差更大）：\n")
    out("| 指标 | linear | nonlinear | 差值(lin-non) | 置换检验 p |")
    out("|---|---|---|---|---|")
    for key, name in (("em", "Exact Match (joint)"), ("em_edges", "Exact Match (edges only)"),
                      ("f1", "F1"), ("nged", "NGED (GED/|E|，次/金标边，越小越好)")):
        lv = [r[key] for r in lin]
        nv = [r[key] for r in non]
        diff, p = paired_permutation_test(nv, lv, n_perm=n_perm, seed=seed)
        out(f"| {name} | {np.mean(lv):.4f} | {np.mean(nv):.4f} | {diff:+.4f} | {p:.4g} |")

    lo, hi = bootstrap_ci([r["em"] for r in lin], seed=seed)
    out(f"\nlinear EM 自助 95%CI: [{fmt_pct(lo)}, {fmt_pct(hi)}]")
    lo, hi = bootstrap_ci([r["em"] for r in non], seed=seed)
    out(f"nonlinear EM 自助 95%CI: [{fmt_pct(lo)}, {fmt_pct(hi)}]\n")

    # 与未配对全量对照
    all_lin = [r for r in records if r["linearity"] == "linear"]
    all_non = [r for r in records if r["linearity"] == "nonlinear"]
    full_gap = mean_of(all_lin, "em") - mean_of(all_non, "em")
    pair_gap = mean_of(lin, "em") - mean_of(non, "em")
    out("**对照**：")
    out(f"- 全量（未控制规模）EM 差距：{full_gap * 100:+.1f} pp"
        f"（linear {fmt_pct(mean_of(all_lin,'em'))} vs nonlinear {fmt_pct(mean_of(all_non,'em'))}）")
    out(f"- 配对（节点数完全相同）EM 差距：{pair_gap * 100:+.1f} pp")
    out(f"- 差距变化：{(pair_gap - full_gap) * 100:+.1f} pp —— "
        f"{'控制了图规模后差距依然存在，说明它不能只归因于图更大' if pair_gap > 0.10 else '控制规模后差距明显缩小，需谨慎解释'}\n")


# ---------------------------------------------------------------- 4. 回归控制

def _gauss_solve(A, b):
    """纯 Python 高斯消元（带部分主元）。

    刻意不使用 numpy 的矩阵运算：部分环境下 numpy 的 BLAS/LAPACK 链接损坏，
    任何矩阵乘法或 np.linalg 调用都会触发原生崩溃（见 ols 的说明）。
    """
    A = [[float(v) for v in row] for row in A]
    b = [float(v) for v in b]
    n = len(A)

    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(A[r][col]))
        if abs(A[piv][col]) < 1e-12:
            raise ValueError("设计矩阵奇异，无法求解")
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
    """最小二乘 + 经典（同方差）标准误。

    刻意**完全不使用 numpy 的矩阵运算**（不用 np.linalg.*，也不用 @ / np.dot）。
    原因：某些环境（本项目的 non_seq 环境实测）numpy 的 BLAS/LAPACK 链接损坏，
    任何矩阵乘法都会触发原生崩溃（Windows fatal exception 0xc06d007f）。
    这里改用纯 Python 列表运算，设计矩阵很小（k ≤ 5，n ≈ 1000），开销可忽略。
    """
    y = [float(v) for v in y]
    X = [[float(v) for v in row] for row in X]
    n = len(X)
    k = len(X[0])

    XtX = [[sum(X[i][a] * X[i][b] for i in range(n)) for b in range(k)] for a in range(k)]
    Xty = [sum(X[i][a] * y[i] for i in range(n)) for a in range(k)]

    beta = _gauss_solve(XtX, Xty)

    resid = [y[i] - sum(X[i][a] * beta[a] for a in range(k)) for i in range(n)]
    dof = max(n - k, 1)
    sigma2 = sum(r * r for r in resid) / dof

    # (X'X)^-1 的对角线：逐列解 X'X z = e_j，只取 z_j
    se = []
    for j in range(k):
        e = [0.0] * k
        e[j] = 1.0
        z = _gauss_solve(XtX, e)
        se.append(math.sqrt(max(sigma2 * z[j], 0.0)))

    ybar = sum(y) / n
    tss = sum((v - ybar) ** 2 for v in y)
    r2 = 1 - sum(r * r for r in resid) / tss if tss > 0 else float("nan")

    out(f"  因变量均值 = {ybar:.4f}，n = {n}，R² = {r2:.4f}")
    out("  | 变量 | 系数 | 标准误 | t |")
    out("  |---|---|---|---|")
    for nm, b, s in zip(names, beta, se):
        t = b / s if s > 0 else float("nan")
        out(f"  | {nm} | {b:+.4f} | {s:.4f} | {t:+.2f} |")
    return beta, se


def regression_control(records, out):
    out("## 4. 回归控制（线性概率模型，因变量 = joint EM）\n")
    out("> 说明：EM 是 0/1 变量，这里用线性概率模型 + 经典标准误；"
        "系数即「非线性相对线性的 EM 百分点差」。若需要更严格的标准误，"
        "可在装有 statsmodels 的环境里改用 Logit + 稳健标准误。\n")

    y = [1.0 if r["em"] else 0.0 for r in records]
    non = [1.0 if r["linearity"] == "nonlinear" else 0.0 for r in records]
    nodes = [float(r["n_nodes"]) for r in records]
    edges = [float(r["n_edges"]) for r in records]
    depth = [float(r["max_depth"]) for r in records]
    one = [1.0] * len(records)

    out("**模型 1**：EM ~ 1 + nonlinear")
    ols(y, np.column_stack([one, non]), ["截距", "nonlinear"], out)

    out("\n**模型 2**：EM ~ 1 + nonlinear + 节点数")
    ols(y, np.column_stack([one, non, nodes]), ["截距", "nonlinear", "节点数"], out)

    out("\n**模型 3**：EM ~ 1 + nonlinear + 节点数 + 边数")
    ols(y, np.column_stack([one, non, nodes, edges]), ["截距", "nonlinear", "节点数", "边数"], out)

    out("\n**模型 4**：EM ~ 1 + nonlinear + 节点数 + 边数 + 嵌套深度")
    ols(y, np.column_stack([one, non, nodes, edges, depth]),
        ["截距", "nonlinear", "节点数", "边数", "嵌套深度"], out)
    out("")


# ---------------------------------------------------------------- 主流程

def preflight():
    """检测 numpy 的 BLAS/LAPACK 是否可用。

    某些环境下 numpy 的矩阵运算会直接触发原生崩溃（Windows fatal exception
    0xc06d007f），而且**无法用 try/except 捕获**——进程会当场死掉、连报错都打不出来。
    所以这里放在子进程里探测，坏环境就给出可执行的解决办法。
    """
    import subprocess

    probe = "import numpy as np; np.zeros((3, 3)) @ np.zeros((3, 3)); np.linalg.inv(np.eye(3))"
    try:
        proc = subprocess.run([sys.executable, "-c", probe],
                              capture_output=True, timeout=60)
    except Exception:                                  # noqa: BLE001
        return
    if proc.returncode != 0:
        print("=" * 72)
        print("警告：当前 Python 环境的 numpy 矩阵运算不可用。")
        print(f"  解释器: {sys.executable}")
        print(f"  探测返回码: {proc.returncode}")
        print("  本脚本的回归部分已改为纯 Python 实现，因此仍可运行；")
        print("  但如果其它步骤也出现进程无故退出，请改用可用的解释器，例如：")
        print("      D:\\Anaconda\\python.exe  (numpy 2.3.5，已实测正常)")
        print("  或修复当前环境：conda install -n non_seq --force-reinstall numpy")
        print("=" * 72)


def main():
    preflight()
    ap = argparse.ArgumentParser(description="线性 vs 非线性脚本的分层对照实验")
    ap.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH)
    ap.add_argument("--results", default=DEFAULT_RESULTS)
    ap.add_argument("--results-label", default=None)
    ap.add_argument("--out", default=None, help="Markdown 输出路径；默认按 results 名派生")
    ap.add_argument("--bin-edges", type=int, nargs="+", default=DEFAULT_BIN_EDGES)
    ap.add_argument("--min-cell", type=int, default=DEFAULT_MIN_CELL)
    ap.add_argument("--permutations", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    label = args.results_label or os.path.splitext(os.path.basename(args.results))[0]
    out_path = args.out or f"stratify_{label}.md"

    lines = []

    def out(s=""):
        # Windows 控制台常见 GBK 代码页，遇到无法编码的字符时降级而不是崩溃
        try:
            print(s)
        except UnicodeEncodeError:
            print(s.encode("gbk", errors="replace").decode("gbk"))
        lines.append(s)

    records, _ = load_records(args.ground_truth, args.results)
    fatal = [r for r in records if r["err"]]
    out(f"# 线性 vs 非线性分层对照 —— {label}\n")
    out(f"- 金标：`{args.ground_truth}`")
    out(f"- 预测：`{args.results}`")
    out(f"- 有效样本：{len(records)} 条")
    if fatal:
        out(f"- ⚠ 其中模型调用失败（按空预测计入）：{len(fatal)} 条 "
            f"（id: {', '.join(str(r['id']) for r in fatal[:20])}）")
    out(f"- 置换次数：{args.permutations}，随机种子：{args.seed}")
    out("")

    describe(records, out)
    stratified_table(records, args.bin_edges, args.min_cell, out)
    matched_analysis(records, args.permutations, args.seed, out)
    regression_control(records, out)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\n[已写入] {out_path}")


if __name__ == "__main__":
    main()
