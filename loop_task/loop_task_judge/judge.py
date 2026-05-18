"""
隐藏答案评估脚本（含混淆统计）
读取 descriptions_dev.json，使用大模型对每个描述进行 0/1/2 判断，
与标准答案对比，输出准确率统计和条件概率混淆矩阵。
"""
import json
import os
import time
from collections import defaultdict
from openai import OpenAI

# ========== 配置 ==========
# INPUT_FILE = "../descriptions_dev.json"
INPUT_FILE = "../generated_backup.json"
PROMPT_FILE = "prompt_judge.txt"
MODEL_NAME = "deepseek-v4-pro"

API_KEY_ENV = "DEEPSEEK_API_KEY"
MAX_RETRIES = 3
RETRY_DELAY = 3
SLEEP_BETWEEN_ITEMS = 1

# ========== 初始化 ==========
api_key = os.environ.get(API_KEY_ENV)
if not api_key:
    raise RuntimeError(f"未找到环境变量 {API_KEY_ENV}")

client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    eval_prompt_template = f.read()

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)


def call_api(messages):
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=messages,
                stream=False,
                extra_body={"thinking": {"type": "enabled"}}
            )
            content = response.choices[0].message.content.strip()
            if content:
                return content
            else:
                print(f"  API 返回空内容，重试 {attempt + 1}/{MAX_RETRIES}")
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"  API 调用异常：{e}，重试 {attempt + 1}/{MAX_RETRIES}")
            time.sleep(RETRY_DELAY)
    return None


def parse_prediction(raw):
    raw = raw.strip()
    try:
        val = int(raw)
        if val in (0, 1, 2):
            return val
    except:
        pass
    import re
    match = re.search(r'\b([012])\b', raw)
    if match:
        return int(match.group(1))
    return None


# ========== 评估主流程 ==========
all_results = []
stats = defaultdict(lambda: defaultdict(int))
# 新增混淆统计：dict[(true_answer, difficulty)][predicted] = count
confusion = defaultdict(lambda: defaultdict(int))

total_items = len(data)
correct_count = 0
total_count = 0

print(f"开始评估 {total_items} 条数据...")

for idx, item in enumerate(data, 1):
    scenario = item["scenario"]
    steps = item["steps"]
    loop_idx = item["loop_idx"]
    loop_step = item["loop_step"]
    descriptions = item["descriptions"]

    steps_text = json.dumps(steps, ensure_ascii=False)
    print(f"\n处理 {idx}/{total_items}: {scenario} (loop: {loop_step})")

    for desc_idx, desc_entry in enumerate(descriptions):
        desc_text = desc_entry[0]
        true_answer = desc_entry[1]
        difficulty = desc_entry[2]

        user_prompt = f"Scenario: \"{scenario}\"\n" \
                      f"Steps: {steps_text}\n" \
                      f"Loop step index: {loop_idx}, loop step content: \"{loop_step}\"\n" \
                      f"Description: \"{desc_text}\""

        messages = [
            {"role": "system", "content": eval_prompt_template},
            {"role": "user", "content": user_prompt}
        ]

        response = call_api(messages)
        if response is None:
            print(f"  描述 {desc_idx + 1}/{len(descriptions)}: API 失败，跳过")
            continue

        pred = parse_prediction(response)
        if pred is None:
            print(f"  描述 {desc_idx + 1}: 无法解析预测值，原始输出：{response[:50]}...")
            continue

        is_correct = (pred == true_answer)
        if is_correct:
            correct_count += 1
        total_count += 1

        all_results.append({
            "item_id": item["id"],
            "scenario": scenario,
            "desc_idx": desc_idx,
            "description": desc_text,
            "true_answer": true_answer,
            "predicted": pred,
            "difficulty": difficulty,
            "correct": is_correct
        })

        # 基本统计
        stats["overall"]["total"] += 1
        if is_correct:
            stats["overall"]["correct"] += 1

        answer_key = f"answer_{true_answer}"
        stats[answer_key]["total"] += 1
        if is_correct:
            stats[answer_key]["correct"] += 1

        diff_key = f"difficulty_{difficulty}" if difficulty != "na" else "difficulty_na"
        stats[diff_key]["total"] += 1
        if is_correct:
            stats[diff_key]["correct"] += 1

        joint_key = f"{answer_key}_{difficulty}"
        stats[joint_key]["total"] += 1
        if is_correct:
            stats[joint_key]["correct"] += 1

        # 混淆矩阵计数
        confusion[(true_answer, difficulty)][pred] += 1

        print(f"  描述 {desc_idx + 1} [{difficulty}] 真实: {true_answer}, "
              f"预测: {pred} {'✓' if is_correct else '✗'}")

    if idx < total_items:
        time.sleep(SLEEP_BETWEEN_ITEMS)

# ========== 输出评估报告 ==========
print("\n" + "=" * 60)
print("评估报告")
print("=" * 60)


def print_stat(name, stat):
    correct = stat.get("correct", 0)
    total = stat.get("total", 0)
    acc = correct / total * 100 if total > 0 else 0
    print(f"{name:30s} : {correct:3d}/{total:3d} = {acc:6.2f}%")


print_stat("Overall", stats["overall"])
print("-" * 40)
for ans in [0, 1, 2]:
    key = f"answer_{ans}"
    if key in stats:
        print_stat(f"  Answer = {ans}", stats[key])
print("-" * 40)
for diff in ["easy", "medium", "hard", "na"]:
    key = f"difficulty_{diff}"
    if key in stats:
        print_stat(f"  Difficulty = {diff}", stats[key])
print("-" * 40)
for ans in [0, 1, 2]:
    for diff in ["easy", "medium", "hard", "na"]:
        joint_key = f"answer_{ans}_{diff}"
        if joint_key in stats and stats[joint_key]["total"] > 0:
            print_stat(f"  Ans={ans}, Diff={diff}", stats[joint_key])

# ========== 追加条件概率混淆矩阵 ==========
print("\n" + "=" * 60)
print("条件概率混淆矩阵 (真实答案 → 预测分布)")
print("=" * 60)

col_width = 9  # 每列宽度（不含列间距）
sep_str = " "  # 列间分隔符


def make_row(first, values):
    """生成对齐的行：第一列左对齐，后续每列右对齐，列间固定分隔符"""
    row = f"{first:<{col_width}}"
    for v in values:
        row += sep_str + f"{v:>{col_width}}"
    return row


for diff in ["easy", "medium", "hard", "na"]:
    print(f"\n难度: {diff}")
    header = make_row("True/Pred", [0, 1, 2, "Total"])
    sep_line = "-" * len(header)
    print(header)
    print(sep_line)

    for true_ans in [0, 1, 2]:
        key = (true_ans, diff)
        total = sum(confusion[key].values())
        if total == 0:
            continue
        counts = [confusion[key].get(p, 0) for p in [0, 1, 2]]
        row = make_row(str(true_ans), counts + [total])
        print(row)

    print(sep_line)
    total_counts = [sum(confusion[(t, diff)].get(p, 0) for t in [0, 1, 2]) for p in [0, 1, 2]]
    total_all = sum(total_counts)
    total_row = make_row("Total", total_counts + [total_all])
    print(total_row)

# 总体混淆矩阵
print("\n" + "=" * 60)
print("总体混淆矩阵（不区分难度）")
print("=" * 60)
header = make_row("True/Pred", [0, 1, 2, "Total"])
sep_line = "-" * len(header)
print(header)
print(sep_line)

for true_ans in [0, 1, 2]:
    counts = []
    for pred_ans in [0, 1, 2]:
        count = 0
        for d in ["easy", "medium", "hard", "na"]:
            count += confusion[(true_ans, d)].get(pred_ans, 0)
        counts.append(count)
    total_true = sum(counts)
    row = make_row(str(true_ans), counts + [total_true])
    print(row)

print(sep_line)
grand_counts = []
for pred_ans in [0, 1, 2]:
    total_pred = 0
    for true_ans in [0, 1, 2]:
        for d in ["easy", "medium", "hard", "na"]:
            total_pred += confusion[(true_ans, d)].get(pred_ans, 0)
    grand_counts.append(total_pred)
grand_total = sum(grand_counts)
total_row = make_row("Total", grand_counts + [grand_total])
print(total_row)

# 保存详细结果
with open("evaluation_details.json", "w", encoding="utf-8") as f:
    json.dump(all_results, f, indent=2, ensure_ascii=False)

print(f"\n详细评估结果已保存至 evaluation_details.json")
print(f"总预测数: {total_count}, 正确数: {correct_count}, 总体准确率: {correct_count / total_count * 100:.2f}%" if total_count > 0 else "")