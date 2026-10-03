import json
import os
import time
from openai import OpenAI
from collections import defaultdict

from metrics import (
    EDGE_METRIC_KEYS,
    TYPE_NAMES,
    compare_graphs,
    compute_edge_metrics,
    compute_statistics,
    get_combo,
    get_purity_group,
    nonlinearity_label,
    print_group,
    print_summary,
    refresh_edge_metrics_in_records,
    summarize_group,
)

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"

_client = None


def get_client():
    """惰性创建 API 客户端。

    不在模块顶层建客户端，这样本文件可以被 import（做离线重算、单测、
    或只复用其评估逻辑）而不要求 DEEPSEEK_API_KEY 已设置。
    """
    global _client
    if _client is None:
        api_key = os.getenv("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("环境变量 DEEPSEEK_API_KEY 未设置，无法调用模型。")
        _client = OpenAI(api_key=api_key, base_url=BASE_URL)
    return _client


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
    response = get_client().chat.completions.create(
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

def collect_used_nodes(struct, used_ids):
    """递归收集 script_graph 中引用到的节点 id（跳过 "continue" 占位符）。"""
    if isinstance(struct, str):
        if struct != "continue":
            used_ids.add(struct)
    elif isinstance(struct, dict):
        if "script" in struct:
            for elem in struct["script"]:
                collect_used_nodes(elem, used_ids)
        elif "options" in struct:
            for opt in struct["options"]:
                collect_used_nodes(opt, used_ids)
        elif "entry" in struct:
            used_ids.add(struct["entry"])
            for elem in struct.get("retry", []):
                collect_used_nodes(elem, used_ids)
            used_ids.add(struct["exit"])
        elif "branches_set" in struct:
            for branch in struct["branches_set"].values():
                for elem in branch:
                    collect_used_nodes(elem, used_ids)


def node_report(ordered_type_cnt, gen_sg, nodes):
    """节点使用报告：missing=金标有但没用上，unused=多出来的，duplicated=重复使用。"""
    node_ids = set(nodes.keys())
    used = []
    collect_used_nodes(gen_sg, used)
    used_set = set(used)

    counts = defaultdict(int)
    for nid in used:
        counts[nid] += 1
    duplicated = sorted(nid for nid, c in counts.items() if c > 1)

    # 金标里允许重复的节点：出现在 loop 结构中的节点
    loop_nodes = set()
    if ordered_type_cnt.get("loop", 0) > 0:
        def walk(s):
            if isinstance(s, dict):
                if s.get("type") == "loop":
                    loop_nodes.add(s.get("entry"))
                    loop_nodes.add(s.get("exit"))
                for k in ("script", "options", "retry"):
                    for e in s.get(k, []) or []:
                        walk(e)
                for b in (s.get("branches_set") or {}).values():
                    for e in b:
                        walk(e)
        walk(gen_sg)

    unexpected_dup = [n for n in duplicated if n not in loop_nodes]
    nodes_valid = (used_set == node_ids) and not unexpected_dup
    return {
        "nodes_valid": nodes_valid,
        "nodes_missing": sorted(node_ids - used_set),
        "nodes_extra": sorted(used_set - node_ids),
        "nodes_duplicated": duplicated,
        "nodes_unexpected_dup": unexpected_dup,
    }


def evaluate_item(item, template, reference_graphs, ordered_type_cnt=None):
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

    node_info = node_report(ordered_type_cnt or {}, gen["script_graph"], nodes)

    return {
        "id": rid,
        "edges_match": edges_match,
        "sg_match": sg_match,
        "generated_edges": gen["edges"],
        "generated_sg": gen["script_graph"],
        **node_info,
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
# 所有指标实现集中在 metrics.py，此处只做编排与落盘，避免口径漂移。

def print_error_report(merged_records):
    """列出模型调用/解析失败的样本——它们会被计入总分母，必须显式披露。"""
    failed = [r for r in merged_records if r.get("error")]
    if not failed:
        print("\n模型调用失败样本: 0 条")
        return
    print(f"\n[!] 模型调用/解析失败样本: {len(failed)} 条（已按空预测计入总分母）")
    for r in failed:
        print(f"    id={r['id']}  {r.get('error')}")


def save_summary(summary):
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    template = load_prompt_template("prompt_predict.txt")

    with open("../introduce/stats/CtrlScript_check_stats_v1.json", "r", encoding="utf-8") as f:
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

    # ---------- 新增：过滤掉标准答案中不存在的 id ----------
    valid_ids = set(reference_stats.keys())
    before_filter = len(merged_records)
    merged_records = [r for r in merged_records if r["id"] in valid_ids]
    skipped = before_filter - len(merged_records)
    if skipped > 0:
        print(f"已跳过 {skipped} 条标准答案中不存在的记录，剩余 {len(merged_records)} 条有效记录。")
    # --------------------------------------------------------

    # 获取已处理id集合，用于跳过
    processed_ids = {r["id"] for r in merged_records}

    for item in dataset:
        rid = item["id"]
        if rid in processed_ids:
            print(f"跳过已处理: {rid}")
            continue

        print(f"Processing {rid}...")
        res = evaluate_item(item, template, reference_graphs, reference_stats[rid]["type_cnt"])

        failed = "error" in res
        if failed:
            # 失败样本保留 error 标记与原始输出，便于事后定位；
            # 指标按空预测计算，并照样计入总分母（与论文口径一致）。
            pred_edges = []
            pred_sg = {"type": "sequence", "script": []}
            print(f"    [!] 生成失败: {res['error']}")
        else:
            pred_edges = res["generated_edges"]
            pred_sg = res["generated_sg"]

        ref_edges = reference_graphs[rid]["edges"]
        edge_metrics = compute_edge_metrics(pred_edges, ref_edges)

        record = {
            "id": rid,
            "scenario": item["scenario"],
            "unordered_nodes": item["unordered_nodes"],
            "edges": pred_edges,
            "script_graph": pred_sg,
            "edges_match": edge_metrics["ged"] == 0,
            "sg_match": res.get("sg_match", False),
            # 记录金标边集，供 NGED 等比率型指标做"总量相除"
            "_ref_edges": ref_edges,
            **{k: v for k, v in res.items() if k.startswith("nodes_")},
            **edge_metrics,
        }
        if failed:
            record["error"] = res["error"]
            record["raw"] = res.get("raw")

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
    print_error_report(merged_records)
    summary = compute_statistics(merged_records, reference_stats)
    if summary:
        print_summary(summary)
        save_summary(summary)
        print(f"\n评估汇总已保存至 {SUMMARY_FILE}")