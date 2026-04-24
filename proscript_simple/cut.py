# 对proscript原始数据集进行简化，仅预留"scenario"，"events","gold_edges_for_prediction"
import os
import json

def clean_data(input_file, output_file):
    # 提取文件所在的目录，创建这个目录
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    with open(input_file, 'r', encoding='utf-8') as f:
        data = json.load(f)

    keys_to_remove = [
        "context", "minutes", "events_minutes",
        "flatten_input_for_edge_prediction", "flatten_input_for_script_generation",
        "flatten_output_for_edge_prediction", "flatten_output_for_script_generation"
    ]

    cleaned_data = []

    for item in data:
        # 1. 删除不需要的顶层字段
        for key in keys_to_remove:
            if key in item:
                del item[key]

        # 2. 处理 events 中的 "NONE" 条目
        events = item.get("events", {})
        # 找出所有值不为 "NONE" 的原始序号（字符串形式的整数）
        valid_old_indices = []
        old_to_new = {}
        new_events = {}
        new_index = 0

        # 按原始序号数字大小排序，保证重编号顺序一致
        for old_idx_str in sorted(events.keys(), key=lambda x: int(x)):
            if events[old_idx_str] != "NONE":
                old_to_new[old_idx_str] = str(new_index)
                new_events[str(new_index)] = events[old_idx_str]
                valid_old_indices.append(old_idx_str)
                new_index += 1

        item["events"] = new_events

        # 3. 处理 gold_edges_for_prediction
        old_edges = item.get("gold_edges_for_prediction", [])
        new_edges = []
        valid_old_set = set(valid_old_indices)  # 用于快速检查

        for edge in old_edges:
            parts = edge.split("->")
            if len(parts) != 2:
                continue  # 格式错误则跳过
            src_old, tgt_old = parts[0], parts[1]
            # 如果源或目标节点是被删除的 "NONE" 节点，则丢弃该边
            if src_old not in valid_old_set or tgt_old not in valid_old_set:
                continue
            # 映射到新序号
            src_new = old_to_new[src_old]
            tgt_new = old_to_new[tgt_old]
            new_edges.append(f"{src_new}->{tgt_new}")

        item["gold_edges_for_prediction"] = new_edges

        cleaned_data.append(item)

    # 写入新文件
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(cleaned_data, f, indent=2, ensure_ascii=False)

    print(f"处理完成！已保存到 {output_file}")

def main():
    in_name=["proscript/dev.json", "proscript/train.json","proscript/test.json"]
    out_name=["proscript_simple/dev.json", "proscript_simple/train.json","proscript_simple/test.json"]
    for i,j in zip(in_name,out_name):
        input_file = i
        output_file = j
        clean_data(input_file, output_file)

if __name__ == "__main__":
    main()
