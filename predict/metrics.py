"""CtrlScript 评测指标（唯一实现，供 predict.py / stratify.py / 后续实验脚本共用）。

集中一份实现，避免同一套公式在多个文件里各写一份而产生口径漂移。

注意：本目录下**不要**新建名为 re.py 的文件——它会遮蔽 Python 标准库的 re 模块，
导致本目录内任何 `import json` 的模块崩溃（json -> re -> 本目录的 re.py）。
原先的 re.py 已删除，其功能并入 predict.py。

关键约定
--------
记号：E 为金标边集，Ê 为模型预测边集。

* GED(Ê, E) = |Ê Δ E| = |Ê \\ E| + |E \\ Ê|
    单位代价的边删除 + 边插入总数，即把预测图变成金标图所需的编辑次数。
    本模块中 GED 是**原始编辑次数**（未归一化），均值以「次」为单位报告。

* e_del = |Ê \\ E|  多预测出来的边（spurious），需要删掉
* e_ins = |E \\ Ê|  漏掉的金标边（missing），需要补上

* 归一化 GED：沿用最初的定义
    NGED = GED / |E| = |Ê Δ E| / |E|
    语义是「**每条金标边平均需要多少次编辑**」，是一个**编辑率**而不是比例——
    分子同时包含漏边与多余边，所以它可以大于 1。
    论文中必须按此解读，不要写成「需要修正的金标边比例」。

* 为了给出真正的比例，另外报告两个分项（分母都是金标边数）：
    missing_rate  = |E \\ Ê| / |E|   漏边占金标边的比例
    spurious_rate = |Ê \\ E| / |E|   多余边相对金标边规模的比例
    两者之和恰为 NGED。若论文需要「需要修正的比例」，应当用 missing_rate。

* IoU（即 Jaccard 系数）= |E ∩ Ê| / |E ∪ Ê|，与 NGED 是**不同**的量
  （分母不同），可以并列报告，不构成重复。

线性 / 非线性划分
----------------
一个样本只要含有 select / loop / and_join 中任意一种（含嵌套）即计入 nonlinear；
三者都没有则为 linear。此外给出互斥细分 linear / select / loop / and_join / mixed。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict

# ---------------------------------------------------------------- 基础定义

TYPE_NAMES = ["select", "loop", "and_join"]

# 逐样本指标的字段顺序（逐条计算后取宏平均）
EDGE_METRIC_KEYS = (
    "precision",
    "recall",
    "f1",
    "iou",
    "ged",
    "e_del",
    "e_ins",
    "nged",
    "missing_rate",
    "spurious_rate",
)

# 比率型指标：除宏平均外，还需要"总量相除"的口径
RATIO_OF_SUMS_KEYS = ("nged", "missing_rate", "spurious_rate")


# ---------------------------------------------------------------- 图与边集

def normalize_graph(edges, script_graph):
    """返回 (排序去重后的边列表, 键序归一化后的 script_graph JSON 字符串)。"""
    normalized_edges = sorted(set(edges or []))
    sg_str = json.dumps(script_graph, sort_keys=True, ensure_ascii=False)
    return normalized_edges, sg_str


def compare_graphs(gen_edges, gen_sg, ref_edges, ref_sg):
    """返回 (edges_match, sg_match)。"""
    gen_e, gen_s = normalize_graph(gen_edges, gen_sg)
    ref_e, ref_s = normalize_graph(ref_edges, ref_sg)
    return gen_e == ref_e, gen_s == ref_s


def compute_edge_ged(pred_edges, ref_edges):
    """边集 GED 及其分项。

    ged   = |Ê Δ E|
    e_del = |Ê \\ E|   预测中多余、需要删除的边
    e_ins = |E \\ Ê|   金标中缺失、需要补上的边
    """
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    e_del = len(pred - ref)
    e_ins = len(ref - pred)
    return {"ged": e_del + e_ins, "e_del": e_del, "e_ins": e_ins}


def compute_normalized_ged(pred_edges, ref_edges):
    """归一化 GED 及其分项。

    nged          = |Ê Δ E| / |E|    每条金标边的平均编辑次数，**可 > 1**
    missing_rate  = |E \\ Ê| / |E|   漏边占金标边比例
    spurious_rate = |Ê \\ E| / |E|   多余边相对金标边规模的比例
    """
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    ged = len(pred ^ ref)
    n_ref = len(ref)

    if n_ref > 0:
        return {
            "nged": ged / n_ref,
            "missing_rate": len(ref - pred) / n_ref,
            "spurious_rate": len(pred - ref) / n_ref,
        }
    # 金标无边：预测也为空才算完美
    n_pred = len(pred)
    return {
        "nged": 0.0 if n_pred == 0 else float(ged),
        "missing_rate": 0.0,
        "spurious_rate": 0.0 if n_pred == 0 else float(n_pred),
    }


def compute_edge_metrics(pred_edges, ref_edges):
    """逐样本边集指标：P / R / F1 / IoU / GED / NGED 及分项。

    分母为 0 时一律记 0.0（不使用未定义值）。
    """
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    n_inter = len(pred & ref)
    n_pred = len(pred)
    n_ref = len(ref)
    n_union = len(pred | ref)

    precision = n_inter / n_pred if n_pred > 0 else 0.0
    recall = n_inter / n_ref if n_ref > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    iou = n_inter / n_union if n_union > 0 else 0.0

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "iou": iou,
        **compute_edge_ged(pred, ref),
        **compute_normalized_ged(pred, ref),
    }


def refresh_edge_metrics_in_records(merged_records, reference_graphs):
    """按 record['edges'] 与金标边集原地重算全部边指标（兼容旧 checkpoint）。"""
    for record in merged_records:
        rid = record.get("id")
        if rid not in reference_graphs:
            continue
        metrics = compute_edge_metrics(record.get("edges", []), reference_graphs[rid]["edges"])
        for key in EDGE_METRIC_KEYS:
            record[key] = metrics[key]
        record["edges_match"] = metrics["ged"] == 0
        record.setdefault("_ref_edges", reference_graphs[rid]["edges"])
    return merged_records


# ---------------------------------------------------------------- 结构分类

def has_nonlinear(type_cnt):
    """是否含有至少一种非线性结构（select / loop / and_join）。"""
    return any(type_cnt.get(t, 0) > 0 for t in TYPE_NAMES)


def nonlinearity_label(type_cnt):
    """二分类标签："linear" 或 "nonlinear"。"""
    return "nonlinear" if has_nonlinear(type_cnt) else "linear"


def get_combo(type_cnt):
    """互斥细分标签：sequence / select / loop / and_join / 组合（按字母序）。"""
    present = sorted(t for t in TYPE_NAMES if type_cnt.get(t, 0) > 0)
    return "+".join(present) if present else "sequence"


def get_purity_group(type_cnt):
    """互斥细分，两种及以上非线性组合统一归为 "mixed"。"""
    present = [t for t in TYPE_NAMES if type_cnt.get(t, 0) > 0]
    if not present:
        return "linear"
    return "mixed" if len(present) >= 2 else present[0]


# ---------------------------------------------------------------- 汇总统计

def _wilson_interval(successes, total, z=1.96):
    """二项比例的 Wilson 置信区间（小样本下比正态近似更稳）。"""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


def summarize_exact_match(records):
    n = len(records)
    if n == 0:
        return {"n": 0, "edges_match_rate": 0.0, "sg_match_rate": 0.0, "both_match_rate": 0.0}
    e_ok = sum(1 for r in records if r.get("edges_match"))
    s_ok = sum(1 for r in records if r.get("sg_match"))
    b_ok = sum(1 for r in records if r.get("edges_match") and r.get("sg_match"))
    lo, hi = _wilson_interval(b_ok, n)
    return {
        "n": n,
        "edges_match_count": e_ok,
        "sg_match_count": s_ok,
        "both_match_count": b_ok,
        "edges_match_rate": e_ok / n,
        "sg_match_rate": s_ok / n,
        "both_match_rate": b_ok / n,
        "both_match_ci95": [lo, hi],
    }


def summarize_edge_metrics_mean(records):
    """逐样本指标取宏平均，并额外给出比率型指标的「总量相除」版本。"""
    n = len(records)
    out = {"n": 0}
    out.update({k: 0.0 for k in EDGE_METRIC_KEYS})
    out.update({f"{k}_ratio_of_sums": 0.0 for k in RATIO_OF_SUMS_KEYS})
    out.update({"n_gold_edges": 0, "n_pred_edges": 0})
    if n == 0:
        return out

    out["n"] = n
    for key in EDGE_METRIC_KEYS:
        out[key] = sum(float(r.get(key, 0.0) or 0.0) for r in records) / n

    ref_total = sum(len(set(r.get("_ref_edges", []) or [])) for r in records)
    pred_total = sum(len(set(r.get("edges", []) or [])) for r in records)
    ged_total = sum(float(r.get("ged", 0) or 0) for r in records)
    miss_total = sum(float(r.get("e_ins", 0) or 0) for r in records)
    spur_total = sum(float(r.get("e_del", 0) or 0) for r in records)

    out["n_gold_edges"] = ref_total
    out["n_pred_edges"] = pred_total
    if ref_total > 0:
        out["nged_ratio_of_sums"] = ged_total / ref_total
        out["missing_rate_ratio_of_sums"] = miss_total / ref_total
        out["spurious_rate_ratio_of_sums"] = spur_total / ref_total
    return out


def summarize_nodes_valid(records):
    """节点使用一致性（只统计真正记录过该字段的样本）。"""
    known = [r for r in records if "nodes_valid" in r]
    if not known:
        return {"n": 0, "nodes_valid_rate": None}
    ok = sum(1 for r in known if r.get("nodes_valid"))
    return {"n": len(known), "nodes_valid_count": ok, "nodes_valid_rate": ok / len(known)}


def summarize_group(records):
    return {
        "exact_match": summarize_exact_match(records),
        "edge_metrics_mean": summarize_edge_metrics_mean(records),
        "nodes_valid": summarize_nodes_valid(records),
    }


def compute_statistics(merged_records, reference_stats):
    """构建 overall / by_linearity / by_depth / by_structure_type / by_combo / by_purity。"""
    if not merged_records:
        return {}

    def type_cnt_of(rid):
        return reference_stats.get(rid, {}).get("type_cnt", {})

    summary = {"overall": summarize_group(merged_records)}

    # 1) 线性 vs 非线性
    by_lin = defaultdict(list)
    for r in merged_records:
        by_lin[nonlinearity_label(type_cnt_of(r["id"]))].append(r)
    summary["by_linearity"] = {k: summarize_group(v) for k, v in by_lin.items()}

    # 2) 嵌套深度
    depth_groups = defaultdict(list)
    for r in merged_records:
        depth = int(reference_stats.get(r["id"], {}).get("max_depth", 0) or 0)
        depth_groups[min(depth, 3)].append(r)
    summary["by_depth"] = {
        (str(d) if d < 3 else "3+"): summarize_group(v) for d, v in sorted(depth_groups.items())
    }

    # 3) 含某类结构（可重叠）
    summary["by_structure_type"] = {}
    for tname in TYPE_NAMES:
        recs = [r for r in merged_records if type_cnt_of(r["id"]).get(tname, 0) > 0]
        if recs:
            summary["by_structure_type"][tname] = summarize_group(recs)

    # 4) 细分组合（互斥，保留组合名）
    combo_groups = defaultdict(list)
    for r in merged_records:
        combo_groups[get_combo(type_cnt_of(r["id"]))].append(r)
    summary["by_combo"] = {k: summarize_group(v) for k, v in sorted(combo_groups.items())}

    # 5) 纯度分组（互斥，多结构合并为 mixed）
    purity_groups = defaultdict(list)
    for r in merged_records:
        purity_groups[get_purity_group(type_cnt_of(r["id"]))].append(r)
    summary["by_purity"] = {k: summarize_group(v) for k, v in sorted(purity_groups.items())}

    return summary


# ---------------------------------------------------------------- 打印

def _pct(x):
    return f"{x * 100:.1f}%" if x is not None else "N/A"


def _fmt_edge_mean(m):
    return (
        f"P={m['precision']*100:.1f}% R={m['recall']*100:.1f}% F1={m['f1']*100:.1f}% "
        f"IoU={m['iou']*100:.1f}% GED={m['ged']:.2f} "
        f"(Del={m['e_del']:.2f} Ins={m['e_ins']:.2f}) "
        f"NGED={m['nged']:.3f} 次/金标边 "
        f"漏边={m['missing_rate']*100:.1f}% 多边={m['spurious_rate']*100:.1f}%"
    )


def print_group(title, grp):
    em, mm = grp["exact_match"], grp["edge_metrics_mean"]
    n = em["n"]
    lo, hi = em.get("both_match_ci95", (0.0, 0.0))
    print(f"   {title} (n={n}):")
    print(f"      完全匹配 Edges: {_pct(em['edges_match_rate'])} ({em.get('edges_match_count', 0)}/{n})")
    print(f"      完全匹配 SG:    {_pct(em['sg_match_rate'])} ({em.get('sg_match_count', 0)}/{n})")
    print(f"      完全匹配 Both:  {_pct(em['both_match_rate'])} ({em.get('both_match_count', 0)}/{n})"
          f"  95%CI=[{lo*100:.1f}%, {hi*100:.1f}%]")
    print(f"      边集均值: {_fmt_edge_mean(mm)}")
    print(f"      总比口径 NGED: {mm.get('nged_ratio_of_sums', 0):.3f} 次/金标边  "
          f"漏边: {_pct(mm.get('missing_rate_ratio_of_sums'))}  "
          f"多边: {_pct(mm.get('spurious_rate_ratio_of_sums'))}")
    nv = grp.get("nodes_valid", {})
    if nv.get("nodes_valid_rate") is not None:
        print(f"      节点集合完全一致: {_pct(nv['nodes_valid_rate'])} ({nv.get('nodes_valid_count', 0)}/{nv['n']})")


def print_summary(summary):
    """把 compute_statistics 的结果打印成人可读报告。"""
    if not summary:
        print("没有数据可供统计。")
        return

    print("=" * 72)
    print("1. 总体")
    print_group("Overall", summary["overall"])

    if "by_linearity" in summary:
        print("\n2. 线性 vs 非线性（含任意一种 select/loop/and_join 即算非线性）")
        for key in ("linear", "nonlinear"):
            if key in summary["by_linearity"]:
                print_group(key, summary["by_linearity"][key])
        lin = summary["by_linearity"].get("linear", {}).get("exact_match", {}).get("both_match_rate")
        non = summary["by_linearity"].get("nonlinear", {}).get("exact_match", {}).get("both_match_rate")
        if lin is not None and non is not None:
            print(f"      >>> 线性-非线性 EM 差距: {(lin - non) * 100:.1f} 个百分点")

    if "by_purity" in summary:
        print("\n3. 结构纯度（互斥；两种及以上非线性归为 mixed）")
        for key, grp in summary["by_purity"].items():
            print_group(key, grp)

    if "by_depth" in summary:
        print("\n4. 各最大嵌套深度")
        for key, grp in summary["by_depth"].items():
            print_group(f"深度 {key}", grp)

    if "by_structure_type" in summary:
        print("\n5. 包含特定非线性结构（可重叠）")
        for key, grp in summary["by_structure_type"].items():
            print_group(f"包含 {key}", grp)

    if "by_combo" in summary:
        print("\n6. 细分结构组合（互斥）")
        for key, grp in summary["by_combo"].items():
            print_group(key, grp)
