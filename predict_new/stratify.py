"""按固定节点数区间比较线性与非线性脚本，只输出三张表。

区间固定为 <=5、6、7、8、>=9，不合并、不抽样。
EM 为边集与 script_graph 同时匹配的比例。其余指标逐脚本计算后宏平均。
EM/P/R/F1/Jaccard 按百分数展示，差值为百分点；GED/NGED 使用原始数值。

用法：
    python stratify.py --results Result/Gemini/run_gemini-3.8-flash.results.json \
        --label gemini_v3 --out Result/Gemini/gemini.md
"""

from __future__ import annotations

import argparse
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from cslib import dataset, metrics, structures  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_GROUND_TRUTH = dataset.DEFAULT_GROUND_TRUTH
BUCKET_LABELS = ("<=5", "6", "7", "8", ">=9")
REPORT_METRICS = (
    ("em", "EM", 100.0),
    ("precision", "精确率", 100.0),
    ("recall", "召回率", 100.0),
    ("f1", "F1", 100.0),
    ("iou", "Jaccard", 100.0),
    ("ged", "GED", 1.0),
    ("nged", "NGED", 1.0),
)


def resolve(path, base=HERE):
    """优先解析当前工作目录的路径，兼容相对脚本目录的旧用法。"""
    if os.path.isabs(path):
        return path
    caller_path = os.path.abspath(path)
    if os.path.exists(caller_path) or os.path.isdir(os.path.dirname(caller_path)):
        return caller_path
    return os.path.join(base, path)


def load_joined(ground_truth_path, results_path):
    """按 ID 合并，使用当前金标和原始预测重算指标。"""
    gold_records, reference_graphs, reference_stats, gold_meta = dataset.load_gold(ground_truth_path)
    gold_by_id = {item["id"]: item for item in gold_records}
    records = dataset.load_records(results_path)
    result_ids = [record.get("id") for record in records]
    duplicate_ids = [rid for rid, count in collections.Counter(result_ids).items() if count > 1]
    if duplicate_ids:
        raise ValueError(f"预测结果包含重复 ID，不能重复计入统计：{duplicate_ids[:10]}")

    result_id_set = set(result_ids)
    gold_id_set = set(gold_by_id)
    gold_meta = dict(gold_meta)
    gold_meta.update({
        "results_n": len(records),
        "results_missing_ids": sorted(gold_id_set - result_id_set),
        "results_extra_ids": sorted(result_id_set - gold_id_set, key=str),
    })
    rows = []
    for record in records:
        rid = record.get("id")
        if rid not in gold_by_id:
            continue
        item = gold_by_id[rid]
        gold = reference_graphs[rid]
        failed = bool(record.get("error"))
        pred_edges = [] if failed else record.get("edges")
        pred_graph = None if failed else record.get("script_graph")
        edge_metrics = metrics.compute_edge_metrics(pred_edges, gold["edges"])
        edges_match, sg_match = metrics.compare_graphs(
            pred_edges, pred_graph, gold["edges"], gold["script_graph"])
        rows.append({
            "id": rid,
            "linearity": structures.nonlinearity_label(reference_stats[rid]["type_cnt"]),
            "n_nodes": len(item["unordered_nodes"]),
            "em": not failed and edges_match and sg_match,
            "precision": edge_metrics["precision"],
            "recall": edge_metrics["recall"],
            "f1": edge_metrics["f1"],
            "iou": edge_metrics["iou"],
            "ged": edge_metrics["ged"],
            "nged": edge_metrics["nged"],
            "failed": failed,
        })
    return rows, gold_meta


def bucket_of(n):
    if n <= 5:
        return "<=5"
    if n >= 9:
        return ">=9"
    return str(n)


def summarize(rows):
    if not rows:
        return {key: None for key, _, _ in REPORT_METRICS}
    return {key: sum(row[key] for row in rows) / len(rows)
            for key, _, _ in REPORT_METRICS}


def format_value(value, scale, signed=False):
    if value is None:
        return "n.a."
    precision = 2 if scale == 100.0 else 4
    scaled = value * scale
    # Avoid displaying a negative zero after rounding.
    if round(scaled, precision) == 0:
        scaled = 0.0
    return f"{scaled:+.{precision}f}" if signed else f"{scaled:.{precision}f}"


def build_report(rows, label, args, out, gold_meta=None):
    gold_meta = gold_meta or {}
    groups = {lab: {"linear": [], "nonlinear": []} for lab in BUCKET_LABELS}
    for row in rows:
        groups[bucket_of(row["n_nodes"])][row["linearity"]].append(row)
    means = {lab: {kind: summarize(group) for kind, group in kinds.items()}
             for lab, kinds in groups.items()}

    out(f"# 节点数分层结果：{label}\n")
    out(f"金标：`{args.ground_truth}`")
    out(f"预测：`{args.results}`")
    out(f"参与统计：{len(rows)} 条。\n")
    missing = gold_meta.get("results_missing_ids", [])
    extra = gold_meta.get("results_extra_ids", [])
    if missing or extra:
        out(f"结果覆盖警告：缺少 {len(missing)} 条金标 ID，多余 {len(extra)} 条预测 ID。"
            "以下数量和指标仅统计已匹配的记录，多余 ID 不计入。\n")
    n_failed = sum(row["failed"] for row in rows)
    if n_failed:
        out(f"其中 {n_failed} 条调用或解析失败，按空预测计入，EM 记为 0。\n")
    out("EM 为边集与 script_graph 同时匹配的比例；精确率、召回率、F1、Jaccard、GED、NGED"
        "均逐脚本计算后宏平均。NGED = GED / 金标边数，可超过 1。")
    out("EM、精确率、召回率、F1、Jaccard 按百分数展示；对应差值为百分点（pp）。"
        "GED、NGED 及其差值使用原始数值；空组及其差值标为 n.a.。\n")

    out("## 1. 脚本数量\n")
    out("| 节点数区间 | 线性 | 非线性 |")
    out("|---|---:|---:|")
    for lab in BUCKET_LABELS:
        out(f"| {lab} | {len(groups[lab]['linear'])} | {len(groups[lab]['nonlinear'])} |")
    out("")

    headers = [f"{name} (%)" if scale == 100.0 else name for _, name, scale in REPORT_METRICS]
    out("## 2. 指标具体值\n")
    out("| 节点数区间 | 类型 | " + " | ".join(headers) + " |")
    out("|---|---|" + "---:|" * len(REPORT_METRICS))
    for lab in BUCKET_LABELS:
        for kind, name in (("linear", "线性"), ("nonlinear", "非线性")):
            values = [format_value(means[lab][kind][key], scale) for key, _, scale in REPORT_METRICS]
            out(f"| {lab} | {name} | " + " | ".join(values) + " |")
    out("")

    headers = [f"Δ{name} (pp)" if scale == 100.0 else f"Δ{name}" for _, name, scale in REPORT_METRICS]
    out("## 3. 指标差值（线性 - 非线性）\n")
    out("| 节点数区间 | " + " | ".join(headers) + " |")
    out("|---|" + "---:|" * len(REPORT_METRICS))
    for lab in BUCKET_LABELS:
        values = []
        for key, _, scale in REPORT_METRICS:
            lin, non = means[lab]["linear"][key], means[lab]["nonlinear"][key]
            diff = lin - non if lin is not None and non is not None else None
            values.append(format_value(diff, scale, signed=True))
        out(f"| {lab} | " + " | ".join(values) + " |")
    out("")


def main(argv=None):
    parser = argparse.ArgumentParser(description="固定五个节点数区间的数量、指标及差值报告",
                                     formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--ground-truth", default=DEFAULT_GROUND_TRUTH, help="金标数据集")
    parser.add_argument("--results", required=True, help="模型预测结果文件")
    parser.add_argument("--label", default=None, help="报告名称")
    parser.add_argument("--out", default=None, help="Markdown 报告输出路径")
    # Accept old commands without running the removed analyses.
    parser.add_argument("--permutations", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--bootstrap", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--seed", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--min-cell", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--bin-edges", type=int, nargs="+", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.bin_edges is not None and args.bin_edges != [0, 5, 6, 7, 8, 99]:
        parser.error("节点数区间已固定为 <=5、6、7、8、>=9，请移除 --bin-edges。")

    args.ground_truth = resolve(args.ground_truth)
    args.results = resolve(args.results)
    label = args.label or os.path.splitext(os.path.basename(args.results))[0]
    out_path = resolve(args.out) if args.out else os.path.join(HERE, f"stratify_{label}.md")
    rows, gold_meta = load_joined(args.ground_truth, args.results)
    if not rows:
        print("没有能与金标按 ID 匹配的预测记录。")
        return 1

    lines = []

    def out(s=""):
        try:
            print(s)
        except UnicodeEncodeError:
            encoding = sys.stdout.encoding or "utf-8"
            print(s.encode(encoding, errors="replace").decode(encoding))
        lines.append(s)

    build_report(rows, label, args, out, gold_meta)
    with open(out_path, "w", encoding="utf-8") as stream:
        stream.write("\n".join(lines) + "\n")
    print(f"\n[已写入] {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
