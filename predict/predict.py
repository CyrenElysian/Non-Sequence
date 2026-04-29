import json
import os
import time
from openai import OpenAI

MODEL = "deepseek-v4-flash"
BASE_URL = "https://api.deepseek.com"
API_KEY = os.getenv("DEEPSEEK_API_KEY")
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# 检查点文件路径
CHECKPOINT_FILE = "eval_checkpoint.json"

def load_prompt_template(filepath):
    with open(filepath, "r", encoding="utf-8") as f:
        return f.read()

# 从数据集中提取三条信息组成一个 JSON 字符串，作为 API 调用的用户消息
# 模型将收到这个 JSON，并需要返回完整的 edges 和 script_graph
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

# 从文本提取 JSON
def extract_json(text):
    if "```json" in text:
        json_str = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        json_str = text.split("```")[1].split("```")[0].strip()
    else:
        json_str = text.strip()
    return json.loads(json_str)

# 对 edges 自动去重并排序 和 对 script_graph 排序
def normalize_graph(edges, script_graph):
    normalized_edges = sorted(set(edges))
    sg_str = json.dumps(script_graph, sort_keys=True, ensure_ascii=False)
    # 把 script_graph 这个嵌套的字典整体转成一个JSON字符串，字典里的键会被按字母排序
    return normalized_edges, sg_str

# 检测 edges 集合是否相等与 script_graph 是否相等
def compare_graphs(gen_edges, gen_sg, ref_edges, ref_sg):
    gen_e, gen_s = normalize_graph(gen_edges, gen_sg)
    ref_e, ref_s = normalize_graph(ref_edges, ref_sg)
    edges_match = gen_e == ref_e
    sg_match = gen_s == ref_s
    return edges_match, sg_match

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
        "reference_edges": ref_edges,
        "reference_sg": ref_sg
    }

def save_checkpoint(results, processed_ids):
    """将当前结果和已处理ID列表写入检查点文件"""
    checkpoint = {
        "results": results,
        "processed_ids": processed_ids
    }
    with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
        json.dump(checkpoint, f, ensure_ascii=False, indent=2)

def load_checkpoint():
    """如果检查点文件存在则加载，否则返回空"""
    if os.path.exists(CHECKPOINT_FILE):
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
        return ckpt.get("results", []), set(ckpt.get("processed_ids", []))
    return [], set()

if __name__ == "__main__":
    template = load_prompt_template("prompt_template.txt")

    with open("../intro_structure/processed_data.json", "r", encoding="utf-8") as f:
        dataset = json.load(f)

    reference_graphs = {}
    for item in dataset:
        reference_graphs[item["id"]] = {
            "edges": item["edges"],
            "script_graph": item["script_graph"]
        }

    # 尝试从检查点恢复
    results, processed_ids = load_checkpoint()
    if results:
        print(f"从检查点恢复，已处理 {len(results)} 条数据。")

    for item in dataset:
        rid = item["id"]
        if rid in processed_ids:
            print(f"跳过已处理: {rid}")
            continue

        print(f"Processing {rid}...")
        res = evaluate_item(item, template, reference_graphs)
        results.append(res)
        processed_ids.add(rid)

        # 每处理完一条立即保存检查点
        save_checkpoint(results, list(processed_ids))
        time.sleep(1)

    # 最终保存完整结果
    with open("evaluation_results.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    # 统计
    total = len(results)
    edges_match = sum(1 for r in results if r.get("edges_match"))
    sg_match = sum(1 for r in results if r.get("sg_match"))
    nodes_valid = sum(1 for r in results if r.get("nodes_valid"))
    print(f"Total: {total}")
    print(f"Edges exact match: {edges_match} ({edges_match/total*100:.1f}%)")
    print(f"Script graph exact match: {sg_match} ({sg_match/total*100:.1f}%)")
    print(f"Nodes valid: {nodes_valid} ({nodes_valid/total*100:.1f}%)")