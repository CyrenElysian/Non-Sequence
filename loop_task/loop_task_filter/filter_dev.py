import json

def is_pure_sequence(script_graph):
    """
    判断 script_graph 是否为纯 sequence 类型
    条件：
    1. type 为 "sequence"
    2. script 列表中所有元素均为字符串（即不包含 and_join 等复杂结构）
    """
    if script_graph.get("type") != "sequence":
        return False
    script_list = script_graph.get("script", [])
    return all(isinstance(item, str) for item in script_list)

def filter_and_transform(input_file, output_file):
    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    filtered = []
    for item in data:
        if "script_graph" not in item:
            continue
        if not is_pure_sequence(item["script_graph"]):
            continue

        # 按照 script 中的顺序提取事件文本
        unordered_nodes = item.get("unordered_nodes", {})
        script_order = item["script_graph"]["script"]
        steps = []
        for node_id in script_order:
            step_text = unordered_nodes.get(node_id, "")
            steps.append(step_text)

        filtered.append({
            "scenario": item["scenario"],
            "steps": steps
        })

    # 重新编号 id（从1开始）
    for new_id, obj in enumerate(filtered, start=1):
        obj["id"] = new_id
        # 调整字段顺序，使 id 在 scenario 前面（可选）
        obj = {"id": obj["id"], "scenario": obj["scenario"], "steps": obj["steps"]}
        filtered[new_id - 1] = obj

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(filtered, f, indent=2, ensure_ascii=False)

    print(f"处理完成！共筛选出 {len(filtered)} 条纯 sequence 数据，已保存至 {output_file}")

if __name__ == "__main__":
    input_filename = "../../correct/llm_fixed_dev.json"
    output_filename = "filter_dev.json"
    filter_and_transform(input_filename, output_filename)