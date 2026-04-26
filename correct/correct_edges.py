# 调用API 修改 converted_dev.json等数据的逻辑错误
# 已截取20条数据进行测试("../temp/temp.json)
import json
import os
import time
from openai import OpenAI

# ================= 配置 =================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY")
if not DEEPSEEK_API_KEY:
    raise RuntimeError("Please set DEEPSEEK_API_KEY environment variable")

client = OpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url="https://api.deepseek.com"
)

# 设置目标输入和输出
PROMPT_TEMPLATE_FILE = "correct/prompt_template.txt"
INPUT_FILE = "convert/converted_dev.json"
OUTPUT_FILE = "convert/llm_fixed_dev.json"
CHANGE_LOG_FILE = "convert/change_logs.json"
SAVE_INTERVAL = 10

# 使用 DeepSeek V4 Flash 模型
MODEL = "deepseek-v4-flash"
TEMPERATURE = 0.1
MAX_TOKENS = 8192
RETRY_TIMES = 3
RETRY_DELAY = 2

# ================= 加载提示模板 =================
with open(PROMPT_TEMPLATE_FILE, "r", encoding="utf-8") as f:
    prompt_template = f.read()


# ================= 辅助函数 =================
def parse_llm_response(content):
    """Enhanced JSON parser with better error handling"""
    content = content.strip()
    # Remove markdown fences
    if content.startswith("```json"):
        content = content[7:]
    elif content.startswith("```"):
        content = content[3:]
    if content.endswith("```"):
        content = content[:-3]
    content = content.strip()

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON object
    import re
    match = re.search(r'\{.*\}', content, re.DOTALL)
    if match:
        candidate = match.group()
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            # Attempt to fix truncated JSON by adding missing braces
            open_braces = candidate.count('{')
            close_braces = candidate.count('}')
            if open_braces > close_braces:
                candidate += '}' * (open_braces - close_braces)
                try:
                    return json.loads(candidate)
                except json.JSONDecodeError:
                    pass
            # Also try to close with `]` if array is open, etc.

    print(f"Failed to parse LLM response: {content[:300]}...")
    raise ValueError("Cannot parse LLM response")

def call_llm(scenario, events, edges, item_id):
    """调用 DeepSeek V4 Flash 修正事件图，返回 (corrected_data, change_log) 或 (None, None)"""
    prompt = prompt_template.format(
        scenario=scenario,
        events_json=json.dumps(events, indent=2),
        edges_json=json.dumps(edges, indent=2)
    )
    for attempt in range(RETRY_TIMES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": "You are a precise JSON generator. Output ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                reasoning_effort="high",
                extra_body={"thinking":{"type":"enabled"}},
                # temperature=TEMPERATURE,
                max_tokens=MAX_TOKENS,
                stream=False
            )
            content = response.choices[0].message.content.strip()
            result = parse_llm_response(content)
            corrected = result.get("corrected_data")
            change_log = result.get("change_log", [])
            # 基本校验
            if isinstance(corrected, dict) and "edges" in corrected:
                corrected["id"] = item_id
                return corrected, change_log
            else:
                print(f"  Missing corrected_data or edges, retry {attempt + 1}")
        except Exception as e:
            print(f"  LLM call failed (attempt {attempt + 1}): {e}")
            if attempt < RETRY_TIMES - 1:
                time.sleep(RETRY_DELAY * (2 ** attempt))  # 指数退避
    return None, None


def load_existing_results(output_path):
    """加载已处理的结果（用于断点续跑）"""
    if os.path.exists(output_path):
        try:
            with open(output_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []
    return []


# ================= 主流程 =================
def main():
    with open(INPUT_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    finished = load_existing_results(OUTPUT_FILE)
    finished_ids = {item["id"] for item in finished}
    change_logs = {}

    total = len(data)
    for idx, item in enumerate(data):
        item_id = item["id"]
        if item_id in finished_ids:
            print(f"Skip {idx + 1}/{total} (ID: {item_id}) - already processed")
            continue

        print(f"Processing {idx + 1}/{total} (ID: {item_id}) - {item['scenario']}")
        corrected, log = call_llm(
            item["scenario"],
            item["unordered_nodes"],
            item["edges"],
            item_id
        )
        if corrected is not None:
            finished.append(corrected)
            finished_ids.add(item_id)
            if log:
                change_logs[str(item_id)] = log
                print(f"  Changes: {log}")
        else:
            print("  LLM failed, keeping original entry.")
            item_original = {
                "id": item["id"],
                "scenario": item["scenario"],
                "unordered_nodes": item["unordered_nodes"],
                "edges": item["edges"],
                "script_graph": item["script_graph"]
            }
            finished.append(item_original)
            finished_ids.add(item_id)

        # 定期保存
        if len(finished) % SAVE_INTERVAL == 0:
            with open(OUTPUT_FILE, "w", encoding="utf-8") as f_out:
                json.dump(finished, f_out, indent=2, ensure_ascii=False)
            # with open(CHANGE_LOG_FILE, "w", encoding="utf-8") as f_log:
            #     json.dump(change_logs, f_log, indent=2, ensure_ascii=False)
            print(f"  Saved {len(finished)} results")

    # 最终保存
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f_out:
        json.dump(finished, f_out, indent=2, ensure_ascii=False)
    # with open(CHANGE_LOG_FILE, "w", encoding="utf-8") as f_log:
    #     json.dump(change_logs, f_log, indent=2, ensure_ascii=False)

    print(f"\nDone! Output: {OUTPUT_FILE}, Change log: {CHANGE_LOG_FILE}")


if __name__ == "__main__":
    main()