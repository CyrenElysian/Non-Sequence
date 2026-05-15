import json
import os
import time
from openai import OpenAI

# ===== 配置 =====
INPUT_FILE = "loop_task_filter/filter_loop_dev.json"
OUTPUT_FILE = "descriptions_dev.json"
BACKUP_FILE = "generated_backup.json"   # 生成过程中的备份文件
MODEL = "deepseek-v4-pro"
PROMPT_DIR = "."

MAX_RETRIES = 3
RETRY_DELAY = 3

# ===== 初始化 =====
api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    raise RuntimeError("DEEPSEEK_API_KEY not set")
client = OpenAI(api_key=api_key, base_url="https://api.deepseek.com")

def load_prompt(filename):
    with open(os.path.join(PROMPT_DIR, filename), "r", encoding="utf-8") as f:
        return f.read()

# 加载生成模板
templates = {
    "cont1": load_prompt("prompt_continue_1.txt"),
    "cont2": load_prompt("prompt_continue_2.txt"),
    "cont3": load_prompt("prompt_continue_3.txt"),
    "stop1": load_prompt("prompt_stop_1.txt"),
    "stop2": load_prompt("prompt_stop_2.txt"),
    "stop3": load_prompt("prompt_stop_3.txt"),
    "na":    load_prompt("prompt_na.txt"),
}

# 加载筛选模板
filter_template = load_prompt("filter_descriptions.txt")

with open(INPUT_FILE, "r", encoding="utf-8") as f:
    data = json.load(f)

def call_api(messages):
    """发送多轮对话，返回模型回复"""
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
            print(f"  API error: {e}, retry {attempt+1}")
            time.sleep(RETRY_DELAY)
    return None

def clean(text):
    return text.strip().strip('"').strip("'").strip()

def parse_filter_response(raw):
    """解析筛选 API 返回的 JSON 对象或 null"""
    if not raw:
        return None
    raw = raw.strip()
    if raw.lower() == "null":
        return None
    # 去除可能的 markdown 代码块
    if raw.startswith("```"):
        lines = raw.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()
    try:
        obj = json.loads(raw)
        if obj is None:
            return None
        return obj
    except:
        return None

# ===== 生成阶段（带增量备份） =====
results = []
total = len(data)

print("=== 开始生成描述 ===")
for idx, item in enumerate(data, 1):
    scenario = item["scenario"]
    steps_json = json.dumps(item["steps"], ensure_ascii=False)
    loop_idx = item["loop_idx"]
    loop_step = item["loop_step"]
    descriptions = []
    success = True

    print(f"\n处理 {idx}/{total}: {scenario}")

    # Continue 链
    prompt1 = templates["cont1"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step)
    msgs_cont = [{"role": "user", "content": prompt1}]
    easy_cont = clean(call_api(msgs_cont) or "")
    if not easy_cont:
        success = False
    else:
        descriptions.append([easy_cont, 1, "easy"])
        print(f"  continue easy: {easy_cont[:60]}...")

        prompt2 = templates["cont2"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_one=easy_cont)
        msgs_cont.append({"role": "assistant", "content": easy_cont})
        msgs_cont.append({"role": "user", "content": prompt2})
        medium_cont = clean(call_api(msgs_cont) or "")
        if not medium_cont:
            success = False
        else:
            descriptions.append([medium_cont, 1, "medium"])
            print(f"  continue medium: {medium_cont[:60]}...")

            prompt3 = templates["cont3"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_two=medium_cont)
            msgs_cont.append({"role": "assistant", "content": medium_cont})
            msgs_cont.append({"role": "user", "content": prompt3})
            hard_cont = clean(call_api(msgs_cont) or "")
            if not hard_cont:
                success = False
            else:
                descriptions.append([hard_cont, 1, "hard"])
                print(f"  continue hard: {hard_cont[:60]}...")

    # Stop 链
    if success:
        prompt1 = templates["stop1"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step)
        msgs_stop = [{"role": "user", "content": prompt1}]
        easy_stop = clean(call_api(msgs_stop) or "")
        if not easy_stop:
            success = False
        else:
            descriptions.append([easy_stop, 0, "easy"])
            print(f"  stop easy: {easy_stop[:60]}...")

            prompt2 = templates["stop2"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_one=easy_stop)
            msgs_stop.append({"role": "assistant", "content": easy_stop})
            msgs_stop.append({"role": "user", "content": prompt2})
            medium_stop = clean(call_api(msgs_stop) or "")
            if not medium_stop:
                success = False
            else:
                descriptions.append([medium_stop, 0, "medium"])
                print(f"  stop medium: {medium_stop[:60]}...")

                prompt3 = templates["stop3"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_two=medium_stop)
                msgs_stop.append({"role": "assistant", "content": medium_stop})
                msgs_stop.append({"role": "user", "content": prompt3})
                hard_stop = clean(call_api(msgs_stop) or "")
                if not hard_stop:
                    success = False
                else:
                    descriptions.append([hard_stop, 0, "hard"])
                    print(f"  stop hard: {hard_stop[:60]}...")

    # NA
    if success:
        prompt_na = templates["na"].format(scenario=scenario, steps=steps_json)
        raw_na = call_api([{"role": "user", "content": prompt_na}])
        if raw_na:
            try:
                na_list = json.loads(raw_na.strip())
                if isinstance(na_list, list) and len(na_list) >= 2:
                    descriptions.append([na_list[0], 2, "na"])
                    descriptions.append([na_list[1], 2, "na"])
                    print("  na: 2条")
                else:
                    success = False
            except:
                success = False
        else:
            success = False

    if success and len(descriptions) == 8:
        entry = {
            "id": item["id"],
            "scenario": scenario,
            "steps": item["steps"],
            "loop_idx": loop_idx,
            "loop_step": loop_step,
            "descriptions": descriptions
        }
        results.append(entry)
        print("  ✓ 成功")
    else:
        print("  ✗ 跳过")

    # ===== 增量备份 =====
    with open(BACKUP_FILE, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)
    print(f"  已备份 {len(results)} 条数据至 {BACKUP_FILE}")

    time.sleep(1)

print(f"\n生成阶段结束，共获得 {len(results)} 条原始描述。")

# ===== 质量筛选阶段 =====
print("\n=== 开始质量筛选 ===")
filtered_results = []

for idx, entry in enumerate(results, 1):
    print(f"筛选 {idx}/{len(results)}: {entry['scenario']} ...", end=" ")
    prompt = filter_template + "\n" + json.dumps(entry, ensure_ascii=False, indent=2)
    raw = call_api([{"role": "user", "content": prompt}])
    if raw:
        cleaned = parse_filter_response(raw)
        if cleaned is not None and "descriptions" in cleaned:
            filtered_results.append(cleaned)
            print(f"✓ 保留 {len(cleaned['descriptions'])} 条描述")
        else:
            print("✗ 丢弃")
    else:
        print("✗ API 失败，丢弃")

    time.sleep(1)

# 重新编号
for new_id, entry in enumerate(filtered_results, start=1):
    entry["id"] = new_id

# 写入最终文件
with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(filtered_results, f, indent=2, ensure_ascii=False)

print(f"\n全部完成！最终保留 {len(filtered_results)} 条高质量数据，已保存至 {OUTPUT_FILE}")