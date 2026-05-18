import json
import os
import time
from openai import OpenAI

# ===== 配置 =====
INPUT_FILE = "filter_loop_dev.json"      # 180条数据
OUTPUT_FILE = "filter_loop_quality_dev.json" # 二次筛选后的高质量数据
BACKUP_FILE = "quality_filter_backup_dev.json" # 断点续传备份
MODEL = "deepseek-v4-pro"
PROMPT_FILE = "filter_loop_quality.txt"

MAX_RETRIES = 3
RETRY_DELAY = 3
STUBBORN_EXTRA_ATTEMPTS = 2
BATCH_SIZE = 10  # 每批处理10条

# ===== 初始化 =====
api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY not set")
client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

def load_prompt(filename):
    with open(filename, "r", encoding="utf-8") as f:
        return f.read()

filter_prompt_template = load_prompt(PROMPT_FILE)

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

def call_api(messages):
    """内部重试"""
    for attempt in range(MAX_RETRIES):
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                stream=False,
                extra_body={"thinking": {"type": "enabled"}},
            )
            content = resp.choices[0].message.content.strip()
            if content:
                return content
        except Exception as e:
            print(f"    API error: {e}, internal retry {attempt+1}")
            time.sleep(RETRY_DELAY)
    return None

def call_api_stubborn(messages):
    """外层额外重试"""
    for extra in range(STUBBORN_EXTRA_ATTEMPTS + 1):
        result = call_api(messages)
        if result is not None:
            return result
        if extra < STUBBORN_EXTRA_ATTEMPTS:
            wait = RETRY_DELAY * (extra + 1) * 2
            print(f"    外层重试 {extra+1}/{STUBBORN_EXTRA_ATTEMPTS}，等待 {wait} 秒...")
            time.sleep(wait)
    return None

def parse_json_array(raw):
    """解析模型返回的JSON数组"""
    if not raw:
        return None
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        return json.loads(raw)
    except:
        import re
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except:
                pass
        return None

# ===== 断点续传 =====
quality_results = []
if os.path.exists(BACKUP_FILE):
    print(f"发现备份文件 {BACKUP_FILE}，加载已有筛选结果...")
    with open(BACKUP_FILE, "r", encoding="utf-8") as f:
        quality_results = json.load(f)
    print(f"已加载 {len(quality_results)} 条筛选结果。")

# 用原始 id 跟踪已筛选的条目
quality_ids = {entry["id"] for entry in quality_results}

# 去重：同一条目可能在备份中重复
seen_ids = set()
unique_results = []
for entry in quality_results:
    if entry["id"] not in seen_ids:
        seen_ids.add(entry["id"])
        unique_results.append(entry)
quality_results = unique_results
quality_ids = seen_ids

# 分批处理
total = len(data)
batches = [data[i:i+BATCH_SIZE] for i in range(0, total, BATCH_SIZE)]
print(f"共 {total} 条数据，分为 {len(batches)} 批处理")

for batch_idx, batch in enumerate(batches):
    # 跳过已全部筛选的批次
    pending = [item for item in batch if item["id"] not in quality_ids]
    if not pending:
        print(f"批次 {batch_idx+1}/{len(batches)}: 全部已筛选，跳过")
        continue

    print(f"\n批次 {batch_idx+1}/{len(batches)}: 处理 {len(pending)} 条待筛选数据...")
    prompt = filter_prompt_template + "\n" + json.dumps(pending, ensure_ascii=False, indent=2)
    raw = call_api_stubborn([{"role": "user", "content": prompt}])

    if raw:
        result_batch = parse_json_array(raw)
        if isinstance(result_batch, list):
            for entry in result_batch:
                if "id" in entry and entry["id"] not in quality_ids:
                    quality_results.append(entry)
                    quality_ids.add(entry["id"])
            print(f"  ✓ 本批保留 {len(result_batch)} 条（累计 {len(quality_results)} 条）")
        else:
            print(f"  ✗ 解析失败，跳过本批")
    else:
        print(f"  ✗ API 失败，跳过本批")

    # 每批结束保存进度
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(quality_results, f, indent=2, ensure_ascii=False)
    print(f"  进度已保存至 {BACKUP_FILE}")

    time.sleep(1)

# 重新编号
for new_id, entry in enumerate(quality_results, start=1):
    entry["id"] = new_id

# 写入最终文件
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(quality_results, f, indent=2, ensure_ascii=False)

print(f"\n全部完成！最终保留 {len(quality_results)} 条高质量循环数据，已保存至 {OUTPUT_FILE}")