import json
import os
import shutil
from collections import defaultdict

# ---------- 路径配置 ----------
CHECKPOINT_FILE = "eval_checkpoint_v4-pro.json"
RESULTS_FILE = "results_v4-pro.json"
GROUND_TRUTH_FILE = "../intro_structure/stats/processed_data_check_with_stats.json"
SUMMARY_FILE = "eval_summary_v4-pro.json"
BACKUP_SUFFIX = ".backup_before_reeval"

# ---------- 图比较与边指标 ----------
def normalize_graph(edges, script_graph):
    return sorted(set(edges)), json.dumps(script_graph, sort_keys=True, ensure_ascii=False)

def compare_graphs(gen_edges, gen_sg, ref_edges, ref_sg):
    gen_e, gen_s = normalize_graph(gen_edges, gen_sg)
    ref_e, ref_s = normalize_graph(ref_edges, ref_sg)
    return gen_e == ref_e, gen_s == ref_s

def compute_edge_ged(pred_edges, ref_edges):
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    e_del = len(pred - ref)
    e_ins = len(ref - pred)
    return {"ged": e_del + e_ins, "e_del": e_del, "e_ins": e_ins}

def compute_edge_metrics(pred_edges, ref_edges):
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    inter = pred & ref
    union = pred | ref
    n_inter, n_pred, n_ref, n_union = len(inter), len(pred), len(ref), len(union)

    precision = n_inter / n_pred if n_pred > 0 else 0.0
    recall = n_inter / n_ref if n_ref > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    iou = n_inter / n_union if n_union > 0 else 0.0
    ged = compute_edge_ged(pred_edges, ref_edges)
    return {"precision": precision, "recall": recall, "f1": f1, "iou": iou, **ged}

# ---------- 汇总统计 ----------
TYPE_NAMES = ["select", "loop", "and_join"]

def get_combo(type_cnt):
    has = [t for t in TYPE_NAMES if type_cnt.get(t, 0) > 0]
    if not has:
        return "sequence"
    has.sort()
    return "+".join(has)

def summarize_exact_match(records):
    n = len(records)
    if n == 0:
        return {"n": 0, "edges_match_rate": 0.0, "sg_match_rate": 0.0, "both_match_rate": 0.0}
    e_ok = sum(1 for r in records if r.get("edges_match"))
    s_ok = sum(1 for r in records if r.get("sg_match"))
    b_ok = sum(1 for r in records if r.get("edges_match") and r.get("sg_match"))
    return {
        "n": n,
        "edges_match_count": e_ok,
        "sg_match_count": s_ok,
        "both_match_count": b_ok,
        "edges_match_rate": e_ok / n,
        "sg_match_rate": s_ok / n,
        "both_match_rate": b_ok / n,
    }

def summarize_edge_metrics_mean(records):
    n = len(records)
    if n == 0:
        return {"n": 0, "precision": 0.0, "recall": 0.0, "f1": 0.0, "iou": 0.0, "ged": 0.0, "e_del": 0.0, "e_ins": 0.0}
    return {
        "n": n,
        "precision": sum(r.get("precision", 0.0) for r in records) / n,
        "recall": sum(r.get("recall", 0.0) for r in records) / n,
        "f1": sum(r.get("f1", 0.0) for r in records) / n,
        "iou": sum(r.get("iou", 0.0) for r in records) / n,
        "ged": sum(r.get("ged", 0) for r in records) / n,
        "e_del": sum(r.get("e_del", 0) for r in records) / n,
        "e_ins": sum(r.get("e_ins", 0) for r in records) / n,
    }

def summarize_group(records):
    return {
        "exact_match": summarize_exact_match(records),
        "edge_metrics_mean": summarize_edge_metrics_mean(records),
    }

def compute_and_print_summary(merged_records, reference_stats):
    total = len(merged_records)
    if total == 0:
        print("没有数据可供统计。")
        return {}

    summary = {"overall": summarize_group(merged_records)}

    def safe_div(num, den):
        return f"{num}/{den}" if den > 0 else "0/0"

    def percent(num, den):
        return f"{num/den*100:.1f}%" if den > 0 else "N/A"

    def fmt_mean(m):
        return (
            f"P={m['precision']*100:.1f}% R={m['recall']*100:.1f}% "
            f"F1={m['f1']*100:.1f}% IoU={m['iou']*100:.1f}% "
            f"GED={m['ged']:.2f} (E-Del={m['e_del']:.2f} E-Ins={m['e_ins']:.2f})"
        )

    em = summary["overall"]["exact_match"]
    mm = summary["overall"]["edge_metrics_mean"]
    print("=" * 60)
    print("1. 总体 — 完全匹配率")
    print(f"   Edges: {percent(em['edges_match_count'], total)} ({safe_div(em['edges_match_count'], total)})")
    print(f"   SG:    {percent(em['sg_match_count'], total)} ({safe_div(em['sg_match_count'], total)})")
    print(f"   Both:  {percent(em['both_match_count'], total)} ({safe_div(em['both_match_count'], total)})")
    print("   边集指标均值:", fmt_mean(mm))

    depth_groups = defaultdict(list)
    for r in merged_records:
        depth = reference_stats.get(r["id"], {}).get("max_depth", 0)
        depth = min(depth, 3)
        depth_groups[depth].append(r)

    summary["by_depth"] = {}
    print("\n2. 各最大嵌套深度")
    for depth in sorted(depth_groups.keys()):
        recs = depth_groups[depth]
        key = str(depth) if depth < 3 else "3+"
        label = f"深度 {depth}" if depth < 3 else "深度 3+"
        grp = summarize_group(recs)
        summary["by_depth"][key] = grp
        em2, mm2 = grp["exact_match"], grp["edge_metrics_mean"]
        n = em2["n"]
        print(f"   {label} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em2['edges_match_count'], n)} ({safe_div(em2['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em2['sg_match_count'], n)} ({safe_div(em2['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em2['both_match_count'], n)} ({safe_div(em2['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm2)}")

    summary["by_structure_type"] = {}
    print("\n3. 包含特定非线性结构")
    for tname in TYPE_NAMES:
        recs = [r for r in merged_records if reference_stats.get(r["id"], {}).get("type_cnt", {}).get(tname, 0) > 0]
        if not recs:
            print(f"   包含 {tname}: 无样本")
            continue
        grp = summarize_group(recs)
        summary["by_structure_type"][tname] = grp
        em2, mm2 = grp["exact_match"], grp["edge_metrics_mean"]
        n = em2["n"]
        print(f"   包含 {tname} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em2['edges_match_count'], n)} ({safe_div(em2['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em2['sg_match_count'], n)} ({safe_div(em2['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em2['both_match_count'], n)} ({safe_div(em2['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm2)}")

    combo_groups = defaultdict(list)
    for r in merged_records:
        combo = get_combo(reference_stats.get(r["id"], {}).get("type_cnt", {}))
        combo_groups[combo].append(r)

    summary["by_combo"] = {}
    print("\n4. 纯顺序与混合结构")
    for combo in sorted(combo_groups.keys()):
        recs = combo_groups[combo]
        grp = summarize_group(recs)
        summary["by_combo"][combo] = grp
        em2, mm2 = grp["exact_match"], grp["edge_metrics_mean"]
        n = em2["n"]
        print(f"   {combo} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em2['edges_match_count'], n)} ({safe_div(em2['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em2['sg_match_count'], n)} ({safe_div(em2['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em2['both_match_count'], n)} ({safe_div(em2['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm2)}")

    return summary

# ---------- 主流程 ----------
def main():
    # 1. 加载 ground truth
    with open(GROUND_TRUTH_FILE, "r", encoding="utf-8") as f:
        dataset = json.load(f)

    reference_graphs = {}
    reference_stats = {}
    for item in dataset:
        rid = item["id"]
        reference_graphs[rid] = {
            "edges": item["edges"],
            "script_graph": item["script_graph"]
        }
        reference_stats[rid] = {
            "max_depth": item.get("max_depth", 0),
            "type_cnt": item.get("type_cnt", {})
        }

    # 2. 加载模型生成的记录
    with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
        all_records = json.load(f)

    print(f"从 {CHECKPOINT_FILE} 加载了 {len(all_records)} 条生成结果。")

    # 备份原文件
    def backup_file(filepath):
        if os.path.exists(filepath):
            backup_path = filepath + BACKUP_SUFFIX
            shutil.copy2(filepath, backup_path)
            print(f"已备份 {filepath} -> {backup_path}")

    backup_file(CHECKPOINT_FILE)
    backup_file(RESULTS_FILE)
    backup_file(SUMMARY_FILE)

    # 3. 过滤：仅保留 id 在 ground truth 中的记录
    valid_records = []
    discarded_count = 0
    for record in all_records:
        rid = record.get("id")
        if rid not in reference_graphs:
            print(f"丢弃无效记录 (标准答案中不存在): {rid}")
            discarded_count += 1
            continue
        valid_records.append(record)

    if discarded_count > 0:
        print(f"共丢弃 {discarded_count} 条记录，保留 {len(valid_records)} 条。")
    else:
        print("所有记录的 id 均存在于标准答案中。")

    # 4. 逐条重新计算指标
    for record in valid_records:
        rid = record["id"]
        # 跳过模型调用失败的错误记录（不更新指标）
        if "error" in record:
            print(f"跳过错误记录 {rid}: {record['error']}")
            continue

        pred_edges = record.get("edges", [])
        pred_sg = record.get("script_graph", {})
        ref_edges = reference_graphs[rid]["edges"]
        ref_sg = reference_graphs[rid]["script_graph"]

        edges_match, sg_match = compare_graphs(pred_edges, pred_sg, ref_edges, ref_sg)
        metrics = compute_edge_metrics(pred_edges, ref_edges)

        record["edges_match"] = edges_match
        record["sg_match"] = sg_match
        record["precision"] = metrics["precision"]
        record["recall"] = metrics["recall"]
        record["f1"] = metrics["f1"]
        record["iou"] = metrics["iou"]
        record["ged"] = metrics["ged"]
        record["e_del"] = metrics["e_del"]
        record["e_ins"] = metrics["e_ins"]

    # 5. 写回过滤并更新后的记录到两个文件
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(valid_records, f, ensure_ascii=False, indent=2)
    print(f"已更新并覆盖 {CHECKPOINT_FILE}（仅保留有效记录）")

    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(valid_records, f, ensure_ascii=False, indent=2)
    print(f"已更新并覆盖 {RESULTS_FILE}（仅保留有效记录）")

    # 6. 重新计算 summary 并覆盖
    summary = compute_and_print_summary(valid_records, reference_stats)
    if summary:
        with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"评估汇总已覆盖 {SUMMARY_FILE}")

if __name__ == "__main__":
    main()