"""
循环步骤检测脚本 v2
- 仅输出含循环步骤的条目，丢弃无循环的条目
- API 调用失败时跳过该批次，不添加任何数据
- 使用 DeepSeek 思考模式
"""
import json
import os
import time
from openai import OpenAI

# ========== 配置 ==========
INPUT_FILE = "filter_dev.json"
OUTPUT_FILE = "filter_loop_dev.json"
PROMPT_FILE = "filter_loop.txt"
MODEL_NAME = "deepseek-v4-pro"

BATCH_SIZE = 50
SLEEP_BETWEEN_BATCHES = 2
MAX_RETRIES = 3

# ========== 初始化客户端 ==========
api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("未找到环境变量 DEEPSEEK_API_KEY")

client = OpenAI(
    api_key=api_key,
    base_url="https://api.deepseek.com"
)

# ========== 加载数据 ==========
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)
print(f"已加载 {len(data)} 条原始数据")

with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    system_prompt = f.read()


# ========== 分批处理 ==========
def process_batch(batch_items, batch_idx, total_batches):
    user_message = json.dumps(batch_items, ensure_ascii=False)

    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                stream=False,
                reasoning_effort="high",
                extra_body={"thinking": {"type": "enabled"}}
            )
            raw_output = response.choices[0].message.content

            if not raw_output:
                print(f"  批次 {batch_idx + 1}: 空响应，重试 {attempt + 1}/{MAX_RETRIES}")
                time.sleep(2)
                continue

            # 清洗 markdown 代码块
            raw_output = raw_output.strip()
            if raw_output.startswith("```"):
                lines = raw_output.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                raw_output = "\n".join(lines).strip()

            result = json.loads(raw_output)

            # 确保结果是列表
            if not isinstance(result, list):
                print(f"  批次 {batch_idx + 1}: 返回不是列表，重试 {attempt + 1}/{MAX_RETRIES}")
                time.sleep(2)
                continue

            return result

        except json.JSONDecodeError as e:
            print(f"  批次 {batch_idx + 1}: JSON 解析失败 (尝试 {attempt + 1}/{MAX_RETRIES})")
            print(f"  返回前200字符: {raw_output[:200]}...")
            time.sleep(2)
        except Exception as e:
            print(f"  批次 {batch_idx + 1}: API 调用异常 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            time.sleep(5)

    print(f"  批次 {batch_idx + 1}: 在 {MAX_RETRIES} 次重试后失败，跳过该批次")
    return None


# ========== 主流程 ==========
total_items = len(data)
total_batches = (total_items + BATCH_SIZE - 1) // BATCH_SIZE
all_results = []
skipped_batches = 0

print(f"开始分批处理，共 {total_batches} 批，每批最多 {BATCH_SIZE} 条")

for batch_idx in range(total_batches):
    start = batch_idx * BATCH_SIZE
    end = min(start + BATCH_SIZE, total_items)
    batch = data[start:end]

    print(f"处理批次 {batch_idx + 1}/{total_batches}（条数 {start + 1}-{end}）...", end=" ")

    result = process_batch(batch, batch_idx, total_batches)

    if result is None:
        print("❌ 跳过")
        skipped_batches += 1
    else:
        # 仅保留含有效 loop_idx（>=0）的条目
        valid_count = 0
        for item in result:
            if isinstance(item.get("loop_idx"), int) and item["loop_idx"] >= 0:
                all_results.append(item)
                valid_count += 1
        print(f"✓ 收到 {len(result)} 条，有效 {valid_count} 条")

    if batch_idx < total_batches - 1:
        time.sleep(SLEEP_BETWEEN_BATCHES)

# ========== 后处理 ==========
# 重新编号 ID（从1开始）
for new_id, item in enumerate(all_results, start=1):
    item["id"] = new_id

# 确保字段顺序
final_results = []
for item in all_results:
    final_results.append({
        "id": item["id"],
        "scenario": item["scenario"],
        "steps": item["steps"],
        "loop_idx": item["loop_idx"],
        "loop_step": item["loop_step"]
    })

# ========== 保存 ==========
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(final_results, f, indent=2, ensure_ascii=False)

print(f"\n处理完成！")
print(f"原始数据: {total_items} 条")
print(f"成功提取循环步骤的数据: {len(final_results)} 条")
print(f"跳过批次: {skipped_batches} 批")
print(f"结果已保存至 {OUTPUT_FILE}")