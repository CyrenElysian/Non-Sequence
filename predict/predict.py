import json
import os
import time
from openai import OpenAI
from collections import defaultdict

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

CHECKPOINT_FILE = "eval_checkpoint_v4-flash.json"
OUTPUT_FILE = "results_v4-flash.json"
SUMMARY_FILE = "eval_summary_v4-flash.json"

# ---------- 辅助函数 ----------
def load_prompt_template(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

def build_user_message(item):
    inp = {
        "id": item["id"],
        "scenario": item["scenario"],
        "unordered_nodes": item["unordered_nodes"]
    }
    return json.dumps(inp, ensure_ascii=False, indent=2)

def call_model(system_prompt, user_message):
    response = client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}},
        stream=False
    )
    return response.choices[0].message.content

def extract_json(text):
    if "```json" in text:
        json_str = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        json_str = text.split("```")[1].split("```")[0].strip()
    else:
        json_str = text.strip()
    return json.loads(json_str)

def normalize_graph(edges, script_graph):
    normalized_edges = sorted(set(edges))
    sg_str = json.dumps(script_graph, sort_keys=True, ensure_ascii=False)
    return normalized_edges, sg_str

def compare_graphs(gen_edges, gen_sg, ref_edges, ref_sg):
    gen_e, gen_s = normalize_graph(gen_edges, gen_sg)
    ref_e, ref_s = normalize_graph(ref_edges, ref_sg)
    edges_match = gen_e == ref_e
    sg_match = gen_s == ref_s
    return edges_match, sg_match

EDGE_METRIC_KEYS = ("precision", "recall", "f1", "iou", "ged", "e_del", "e_ins")


def compute_edge_ged(pred_edges, ref_edges):
    """
    边集 GED（仅增/删边，代价均为 1）：预测边集 P -> 参考边集 R。
    ged = |P \\ R| + |R \\ P|；e_del=需删边数，e_ins=需增边数。
    """
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    e_del = len(pred - ref)
    e_ins = len(ref - pred)
    return {"ged": e_del + e_ins, "e_del": e_del, "e_ins": e_ins}


def compute_edge_metrics(pred_edges, ref_edges):
    """边集 Precision / Recall / F1 / IoU / GED；分母为 0 时记 0.0。"""
    pred = set(pred_edges or [])
    ref = set(ref_edges or [])
    inter = pred & ref
    union = pred | ref
    n_inter = len(inter)
    n_pred = len(pred)
    n_ref = len(ref)
    n_union = len(union)

    precision = n_inter / n_pred if n_pred > 0 else 0.0
    recall = n_inter / n_ref if n_ref > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    iou = n_inter / n_union if n_union > 0 else 0.0
    ged = compute_edge_ged(pred_edges, ref_edges)
    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "iou": iou,
        **ged,
    }


def zero_edge_metrics():
    """参考边集也为空时的占位指标。"""
    return {
        "precision": 0.0,
        "recall": 0.0,
        "f1": 0.0,
        "iou": 0.0,
        "ged": 0,
        "e_del": 0,
        "e_ins": 0,
    }


def refresh_edge_metrics_in_records(merged_records, reference_graphs):
    """按 record['edges'] 与参考边集重新计算指标（兼容旧 checkpoint）。"""
    for record in merged_records:
        rid = record.get("id")
        if rid not in reference_graphs:
            continue
        pred_edges = record.get("edges", [])
        ref_edges = reference_graphs[rid]["edges"]
        metrics = compute_edge_metrics(pred_edges, ref_edges)
        for key in EDGE_METRIC_KEYS:
            record[key] = metrics[key]
        record["edges_match"] = metrics["ged"] == 0

def evaluate_item(item, template, reference_graphs):
    rid = item["id"]
    nodes = item["unordered_nodes"]
    user_msg = build_user_message(item)
    raw = call_model(template, user_msg)
    try:
        gen = extract_json(raw)
    except Exception as e:
        return {"id": rid, "error": f"JSON parse error: {e}", "raw": raw}

    if "edges" not in gen or "script_graph" not in gen:
        return {"id": rid, "error": "Missing edges or script_graph", "raw": raw}

    ref_edges = reference_graphs[rid]["edges"]
    ref_sg = reference_graphs[rid]["script_graph"]
    edges_match, sg_match = compare_graphs(gen["edges"], gen["script_graph"], ref_edges, ref_sg)
    edge_metrics = compute_edge_metrics(gen["edges"], ref_edges)

    # 节点使用一致性检查
    node_ids = set(nodes.keys())
    used_ids = set()
    def collect_nodes(struct):
        if isinstance(struct, str):
            if struct != "continue":
                used_ids.add(struct)
        elif isinstance(struct, dict):
            if "script" in struct:
                for elem in struct["script"]:
                    collect_nodes(elem)
            elif "options" in struct:
                for opt in struct["options"]:
                    collect_nodes(opt)
            elif "entry" in struct:
                used_ids.add(struct["entry"])
                if "retry" in struct:
                    for elem in struct["retry"]:
                        collect_nodes(elem)
                used_ids.add(struct["exit"])
            elif "branches_set" in struct:
                for branch in struct["branches_set"].values():
                    for elem in branch:
                        collect_nodes(elem)
    collect_nodes(gen["script_graph"])
    nodes_valid = (used_ids == node_ids)

    return {
        "id": rid,
        "edges_match": edges_match,
        "sg_match": sg_match,
        "nodes_valid": nodes_valid,
        "generated_edges": gen["edges"],
        "generated_sg": gen["script_graph"],
        **edge_metrics,
    }

def save_checkpoint(merged_records):
    """保存已处理的合并记录到检查点文件"""
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged_records, f, ensure_ascii=False, indent=2)

def load_checkpoint():
    """如果检查点文件存在则加载已合并的记录列表，否则返回空列表"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []

# ---------- 指标计算 ----------
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
        return {
            "n": 0,
            "precision": 0.0,
            "recall": 0.0,
            "f1": 0.0,
            "iou": 0.0,
            "ged": 0.0,
            "e_del": 0.0,
            "e_ins": 0.0,
        }
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

def compute_statistics(merged_records, reference_stats):
    """计算汇总指标，打印并返回可写入 eval_summary.json 的结构。"""
    print("=" * 60)
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
    print("1. 总体 — 完全匹配率")
    print(f"   Edges: {percent(em['edges_match_count'], total)} ({safe_div(em['edges_match_count'], total)})")
    print(f"   SG:    {percent(em['sg_match_count'], total)} ({safe_div(em['sg_match_count'], total)})")
    print(f"   Both:  {percent(em['both_match_count'], total)} ({safe_div(em['both_match_count'], total)})")
    print("   边集指标均值:", fmt_mean(mm))

    depth_groups = defaultdict(list)
    for r in merged_records:
        rid = r["id"]
        depth = reference_stats.get(rid, {}).get("max_depth", 0)
        if depth >= 3:
            depth = 3
        depth_groups[depth].append(r)

    summary["by_depth"] = {}
    print("\n2. 各最大嵌套深度")
    for depth in sorted(depth_groups.keys()):
        recs = depth_groups[depth]
        key = str(depth) if depth < 3 else "3+"
        label = f"深度 {depth}" if depth < 3 else "深度 3+"
        grp = summarize_group(recs)
        summary["by_depth"][key] = grp
        em, mm = grp["exact_match"], grp["edge_metrics_mean"]
        n = em["n"]
        print(f"   {label} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em['edges_match_count'], n)} ({safe_div(em['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em['sg_match_count'], n)} ({safe_div(em['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em['both_match_count'], n)} ({safe_div(em['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm)}")

    summary["by_structure_type"] = {}
    print("\n3. 包含特定非线性结构")
    for tname in TYPE_NAMES:
        recs = [
            r for r in merged_records
            if reference_stats.get(r["id"], {}).get("type_cnt", {}).get(tname, 0) > 0
        ]
        if not recs:
            print(f"   包含 {tname}: 无样本")
            continue
        grp = summarize_group(recs)
        summary["by_structure_type"][tname] = grp
        em, mm = grp["exact_match"], grp["edge_metrics_mean"]
        n = em["n"]
        print(f"   包含 {tname} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em['edges_match_count'], n)} ({safe_div(em['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em['sg_match_count'], n)} ({safe_div(em['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em['both_match_count'], n)} ({safe_div(em['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm)}")

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
        em, mm = grp["exact_match"], grp["edge_metrics_mean"]
        n = em["n"]
        print(f"   {combo} (n={n}):")
        print(f"      完全匹配 Edges: {percent(em['edges_match_count'], n)} ({safe_div(em['edges_match_count'], n)})")
        print(f"      完全匹配 SG:    {percent(em['sg_match_count'], n)} ({safe_div(em['sg_match_count'], n)})")
        print(f"      完全匹配 Both:  {percent(em['both_match_count'], n)} ({safe_div(em['both_match_count'], n)})")
        print(f"      边集均值: {fmt_mean(mm)}")

    return summary

def save_summary(summary):
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    template = load_prompt_template("prompt_predict.txt")

    with open("../intro_structure/stats/processed_data_check_with_stats.json", "r", encoding="utf-8") as f:
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

    # 从断点恢复
    merged_records = load_checkpoint()
    if merged_records:
        print(f"从检查点恢复，已处理 {len(merged_records)} 条数据。")
        refresh_edge_metrics_in_records(merged_records, reference_graphs)

    # 获取已处理id集合，用于跳过
    processed_ids = {r["id"] for r in merged_records}

    for item in dataset:
        rid = item["id"]
        if rid in processed_ids:
            print(f"跳过已处理: {rid}")
            continue

        print(f"Processing {rid}...")
        res = evaluate_item(item, template, reference_graphs)

        pred_edges = res.get("generated_edges", []) if "error" not in res else []
        ref_edges = reference_graphs[rid]["edges"]
        edge_metrics = compute_edge_metrics(pred_edges, ref_edges)

        record = {
            "id": rid,
            "scenario": item["scenario"],
            "unordered_nodes": item["unordered_nodes"],
            "edges": pred_edges,
            "script_graph": res.get("generated_sg", {"type": "sequence", "script": []}) if "error" not in res else {"type": "sequence", "script": []},
            "edges_match": edge_metrics["ged"] == 0,
            "sg_match": res.get("sg_match", False),
            **edge_metrics,
        }

        merged_records.append(record)
        processed_ids.add(rid)

        # 实时写入检查点
        save_checkpoint(merged_records)
        time.sleep(1)

    # 最终写入合并文件
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(merged_records, f, ensure_ascii=False, indent=2)

    print(f"\n合并文件已保存至 {OUTPUT_FILE}")
    refresh_edge_metrics_in_records(merged_records, reference_graphs)
    summary = compute_statistics(merged_records, reference_stats)
    if summary:
        save_summary(summary)
        print(f"评估汇总已保存至 {SUMMARY_FILE}")