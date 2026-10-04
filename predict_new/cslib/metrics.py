r"""评测指标：边集 P/R/F1/IoU、GED 及归一化变体、分组汇总。

与旧版 predict/metrics.py 的差异（均为修正）：
  1. **不再从记录里读 `_ref_edges`**。比率型指标（NGED 等）的分母直接取自
     当前金标 `reference_graphs`，避免金标修订后"逐条指标用新金标、
     比率指标用旧金标"的口径脱钩。
  2. 新增 `structure_errors` 的结构合法性统计（structural-validity rate），
     用于区分"结构非法"与"结构合法但选错"两类失败。
  3. 所有汇总函数都显式接收 reference_graphs，不依赖记录内的冗余字段。

指标口径
--------
记号：E 为金标边集，Ê 为预测边集。

  GED  = |Ê Δ E|                 原始编辑次数（单位代价增删边）
  e_del= |Ê \ E|                 多预测出来的边
  e_ins= |E \ Ê|                 漏掉的金标边
  NGED = GED / |E|               **每条金标边平均需要多少次编辑**（编辑率，可 > 1）
  missing_rate  = |E \ Ê| / |E|  漏边占金标边的比例
  spurious_rate = |Ê \ E| / |E|  多余边相对金标边规模的比例
  IoU  = |E ∩ Ê| / |E ∪ Ê|       即 Jaccard 系数（与 NGED 分母不同，非重复）

NGED 是编辑率而非比例，论文中必须按此表述；需要"比例"时用 missing_rate。
P / R / F1 为**逐条计算后的宏平均**；比率型指标另给"总量相除"口径。
"""

from __future__ import annotations

import json
import math
from collections import defaultdict

TYPE_NAMES = ("select", "loop", "and_join")

EDGE_METRIC_KEYS = (
    "precision", "recall", "f1", "iou",
    "ged", "e_del", "e_ins",
    "nged", "missing_rate", "spurious_rate",
)

RATIO_OF_SUMS_KEYS = ("nged", "missing_rate", "spurious_rate")


# ---------------------------------------------------------------- 图与边集

def normalize_graph(edges, script_graph):
    """(排序去重后的边列表, 键序归一化后的 script_graph JSON 字符串)。"""
    return sorted(set(edges or [])), json.dumps(script_graph, sort_keys=True, ensure_ascii=False)


def compare_graphs(pred_edges, pred_sg, gold_edges, gold_sg):
    """返回 (edges_match, sg_match)。"""
    pe, ps = normalize_graph(pred_edges, pred_sg)
    ge, gs = normalize_graph(gold_edges, gold_sg)
    return pe == ge, ps == gs


def compute_edge_ged(pred_edges, gold_edges):
    pred, gold = set(pred_edges or []), set(gold_edges or [])
    e_del, e_ins = len(pred - gold), len(gold - pred)
    return {"ged": e_del + e_ins, "e_del": e_del, "e_ins": e_ins}


def compute_normalized_ged(pred_edges, gold_edges):
    pred, gold = set(pred_edges or []), set(gold_edges or [])
    ged = len(pred ^ gold)
    n_gold = len(gold)
    if n_gold > 0:
        return {
            "nged": ged / n_gold,
            "missing_rate": len(gold - pred) / n_gold,
            "spurious_rate": len(pred - gold) / n_gold,
        }
    # 金标无边：预测也为空才算完美
    n_pred = len(pred)
    return {
        "nged": 0.0 if n_pred == 0 else float(ged),
        "missing_rate": 0.0,
        "spurious_rate": 0.0 if n_pred == 0 else float(n_pred),
    }


def compute_edge_metrics(pred_edges, gold_edges):
    """逐样本边集指标。分母为 0 时一律记 0.0。"""
    pred, gold = set(pred_edges or []), set(gold_edges or [])
    n_inter, n_pred, n_gold = len(pred & gold), len(pred), len(gold)
    n_union = len(pred | gold)

    precision = n_inter / n_pred if n_pred > 0 else 0.0
    recall = n_inter / n_gold if n_gold > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    iou = n_inter / n_union if n_union > 0 else 0.0

    return {
        "precision": precision, "recall": recall, "f1": f1, "iou": iou,
        **compute_edge_ged(pred, gold),
        **compute_normalized_ged(pred, gold),
    }


# ---------------------------------------------------------------- 比例与区间

def wilson_interval(successes, total, z=1.96):
    """二项比例的 Wilson 置信区间（小样本下比正态近似稳）。"""
    if total <= 0:
        return (0.0, 0.0)
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return (max(0.0, center - half), min(1.0, center + half))


# ---------------------------------------------------------------- 汇总

def _gold_edge_total(records, reference_graphs):
    """比率型指标的分母：按当前金标统计的总边数。"""
    if reference_graphs is None:
        return None
    return sum(len(set(reference_graphs[r["id"]]["edges"])) for r in records
               if r.get("id") in reference_graphs)


def summarize_exact_match(records):
    n = len(records)
    if n == 0:
        return {"n": 0, "edges_match_rate": 0.0, "sg_match_rate": 0.0, "both_match_rate": 0.0}
    e_ok = sum(1 for r in records if r.get("edges_match"))
    s_ok = sum(1 for r in records if r.get("sg_match"))
    b_ok = sum(1 for r in records if r.get("edges_match") and r.get("sg_match"))
    lo, hi = wilson_interval(b_ok, n)
    return {
        "n": n,
        "edges_match_count": e_ok, "sg_match_count": s_ok, "both_match_count": b_ok,
        "edges_match_rate": e_ok / n, "sg_match_rate": s_ok / n, "both_match_rate": b_ok / n,
        "both_match_ci95": [lo, hi],
    }


def summarize_edge_metrics_mean(records, reference_graphs=None):
    """逐条宏平均 + 比率型指标的"总量相除"口径。"""
    n = len(records)
    out = {"n": 0}
    out.update({k: 0.0 for k in EDGE_METRIC_KEYS})
    out.update({f"{k}_ratio_of_sums": None for k in RATIO_OF_SUMS_KEYS})
    out["n_gold_edges"] = 0
    out["n_pred_edges"] = 0
    if n == 0:
        return out

    out["n"] = n
    for k in EDGE_METRIC_KEYS:
        out[k] = sum(float(r.get(k, 0.0) or 0.0) for r in records) / n

    pred_total = sum(len(set(r.get("edges") or [])) for r in records)
    ged_total = sum(float(r.get("ged", 0) or 0) for r in records)
    miss_total = sum(float(r.get("e_ins", 0) or 0) for r in records)
    spur_total = sum(float(r.get("e_del", 0) or 0) for r in records)
    gold_total = _gold_edge_total(records, reference_graphs)

    out["n_pred_edges"] = pred_total
    if gold_total is not None:
        out["n_gold_edges"] = gold_total
        if gold_total > 0:
            out["nged_ratio_of_sums"] = ged_total / gold_total
            out["missing_rate_ratio_of_sums"] = miss_total / gold_total
            out["spurious_rate_ratio_of_sums"] = spur_total / gold_total
    return out


def summarize_nodes_valid(records):
    known = [r for r in records if "nodes_valid" in r]
    if not known:
        return {"n": 0, "nodes_valid_rate": None}
    ok = sum(1 for r in known if r.get("nodes_valid"))
    return {"n": len(known), "nodes_valid_count": ok, "nodes_valid_rate": ok / len(known)}


def summarize_structure_validity(records):
    """模型输出结构的合法性：结构非法 vs 合法但选错，是两类不同失败。"""
    known = [r for r in records if "structure_errors" in r]
    if not known:
        return {"n": 0, "structure_valid_rate": None}
    ok = sum(1 for r in known if not r.get("structure_errors"))
    lo, hi = wilson_interval(ok, len(known))
    return {
        "n": len(known),
        "structure_valid_count": ok,
        "structure_valid_rate": ok / len(known),
        "structure_valid_ci95": [lo, hi],
    }


def summarize_failures(records):
    """模型调用/解析失败的样本（按空预测计入分母，但必须显式披露）。"""
    failed = [r for r in records if r.get("error")]
    return {"n_failed": len(failed), "n_total": len(records), "ids": [r["id"] for r in failed]}


def summarize_group(records, reference_graphs=None):
    return {
        "exact_match": summarize_exact_match(records),
        "edge_metrics_mean": summarize_edge_metrics_mean(records, reference_graphs),
        "nodes_valid": summarize_nodes_valid(records),
        "structure_validity": summarize_structure_validity(records),
    }


# ---------------------------------------------------------------- 分组统计

def compute_statistics(records, reference_stats, reference_graphs=None):
    """overall / by_linearity / by_purity / by_depth / by_structure_type / by_combo。"""
    if not records:
        return {}

    def tc_of(rid):
        return reference_stats.get(rid, {}).get("type_cnt", {})

    from .structures import combo_label, nonlinearity_label, purity_label

    summary = {
        "overall": summarize_group(records, reference_graphs),
        "failures": summarize_failures(records),
    }

    by_lin = defaultdict(list)
    for r in records:
        by_lin[nonlinearity_label(tc_of(r["id"]))].append(r)
    summary["by_linearity"] = {k: summarize_group(v, reference_graphs) for k, v in by_lin.items()}

    by_purity = defaultdict(list)
    for r in records:
        by_purity[purity_label(tc_of(r["id"]))].append(r)
    summary["by_purity"] = {k: summarize_group(v, reference_graphs) for k, v in sorted(by_purity.items())}

    depth_groups = defaultdict(list)
    for r in records:
        d = int(reference_stats.get(r["id"], {}).get("max_depth", 0) or 0)
        depth_groups[min(d, 3)].append(r)
    summary["by_depth"] = {
        (str(d) if d < 3 else "3+"): summarize_group(v, reference_graphs)
        for d, v in sorted(depth_groups.items())
    }

    summary["by_structure_type"] = {}
    for t in TYPE_NAMES:
        recs = [r for r in records if tc_of(r["id"]).get(t, 0) > 0]
        if recs:
            summary["by_structure_type"][t] = summarize_group(recs, reference_graphs)

    by_combo = defaultdict(list)
    for r in records:
        by_combo[combo_label(tc_of(r["id"]))].append(r)
    summary["by_combo"] = {k: summarize_group(v, reference_graphs) for k, v in sorted(by_combo.items())}

    return summary


# ---------------------------------------------------------------- 打印

def _pct(x):
    return "N/A" if x is None else f"{x * 100:.1f}%"


def _fmt_edge_mean(m):
    return (
        f"P={m['precision']*100:.1f}% R={m['recall']*100:.1f}% F1={m['f1']*100:.1f}% "
        f"IoU={m['iou']*100:.1f}% GED={m['ged']:.2f} "
        f"(Del={m['e_del']:.2f} Ins={m['e_ins']:.2f}) "
        f"NGED={m['nged']:.3f}次/金标边 漏边={m['missing_rate']*100:.1f}% 多边={m['spurious_rate']*100:.1f}%"
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
    print(f"      总比口径 NGED: {mm.get('nged_ratio_of_sums') if mm.get('nged_ratio_of_sums') is None else round(mm['nged_ratio_of_sums'], 3)}"
          f"  漏边: {_pct(mm.get('missing_rate_ratio_of_sums'))}"
          f"  多边: {_pct(mm.get('spurious_rate_ratio_of_sums'))}")
    sv = grp.get("structure_validity", {})
    if sv.get("structure_valid_rate") is not None:
        print(f"      结构合法率: {_pct(sv['structure_valid_rate'])} "
              f"({sv.get('structure_valid_count', 0)}/{sv['n']})")
    nv = grp.get("nodes_valid", {})
    if nv.get("nodes_valid_rate") is not None:
        print(f"      节点集合一致: {_pct(nv['nodes_valid_rate'])} "
              f"({nv.get('nodes_valid_count', 0)}/{nv['n']})")


def print_summary(summary):
    if not summary:
        print("没有数据可供统计。")
        return
    print("=" * 76)
    print("1. 总体")
    print_group("Overall", summary["overall"])

    f = summary.get("failures", {})
    if f.get("n_failed"):
        print(f"      [!] 失败样本 {f['n_failed']}/{f['n_total']}（按空预测计入分母）: {f['ids']}")

    if "by_linearity" in summary:
        print("\n2. 线性 vs 非线性")
        for k in ("linear", "nonlinear"):
            if k in summary["by_linearity"]:
                print_group(k, summary["by_linearity"][k])
        lin = summary["by_linearity"].get("linear", {}).get("exact_match", {}).get("both_match_rate")
        non = summary["by_linearity"].get("nonlinear", {}).get("exact_match", {}).get("both_match_rate")
        if lin is not None and non is not None:
            print(f"      >>> 线性-非线性 EM 差距: {(lin - non) * 100:.1f} 个百分点")

    for key, title in (("by_purity", "3. 结构纯度（互斥，多结构归 mixed）"),
                       ("by_depth", "4. 各最大嵌套深度"),
                       ("by_structure_type", "5. 包含特定非线性结构（可重叠）"),
                       ("by_combo", "6. 细分结构组合（互斥）")):
        if key in summary:
            print(f"\n{title}")
            for k, grp in summary[key].items():
                print_group(f"深度 {k}" if key == "by_depth" else k, grp)
