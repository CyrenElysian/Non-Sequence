import json, os, time
from openai import OpenAI

# ===== 配置 =====
INPUT_FILE = "filter_loop_dev.json"
OUTPUT_FILE = "descriptions_dev.json"
MODEL = "deepseek-v4-flash"
PROMPT_DIR = "."  # 提示词文件所在目录

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

# 加载所有模板
templates = {
    "cont1": load_prompt("prompt_continue_1.txt"),
    "cont2": load_prompt("prompt_continue_2.txt"),
    "cont3": load_prompt("prompt_continue_3.txt"),
    "stop1": load_prompt("prompt_stop_1.txt"),
    "stop2": load_prompt("prompt_stop_2.txt"),
    "stop3": load_prompt("prompt_stop_3.txt"),
    "na":    load_prompt("prompt_na.txt"),
}

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

results = []
total = len(data)

for idx, item in enumerate(data, 1):
    scenario = item["scenario"]
    steps_json = json.dumps(item["steps"], ensure_ascii=False)
    loop_idx = item["loop_idx"]
    loop_step = item["loop_step"]
    descriptions = []
    success = True

    print(f"\n处理 {idx}/{total}: {scenario}")

    # ========== Continue 链 ==========
    # 第一轮
    prompt1 = templates["cont1"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step)
    msgs_cont = [{"role": "user", "content": prompt1}]
    easy_cont = clean(call_api(msgs_cont) or "")
    if not easy_cont:
        success = False
    else:
        descriptions.append([easy_cont, 1, "easy"])
        print(f"  continue easy: {easy_cont[:60]}...")

        # 第二轮：基于 easy 输出，要求更深原因
        prompt2 = templates["cont2"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_one=easy_cont)
        msgs_cont.append({"role": "assistant", "content": easy_cont})
        msgs_cont.append({"role": "user", "content": prompt2})
        medium_cont = clean(call_api(msgs_cont) or "")
        if not medium_cont:
            success = False
        else:
            descriptions.append([medium_cont, 1, "medium"])
            print(f"  continue medium: {medium_cont[:60]}...")

            # 第三轮：基于 medium 输出，要求更更深原因
            prompt3 = templates["cont3"].format(scenario=scenario, steps=steps_json, loop_idx=loop_idx, loop_step=loop_step, reason_two=medium_cont)
            msgs_cont.append({"role": "assistant", "content": medium_cont})
            msgs_cont.append({"role": "user", "content": prompt3})
            hard_cont = clean(call_api(msgs_cont) or "")
            if not hard_cont:
                success = False
            else:
                descriptions.append([hard_cont, 1, "hard"])
                print(f"  continue hard: {hard_cont[:60]}...")

    # ========== Stop 链 ==========
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

    # ========== NA ==========
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
        results.append({
            "id": item["id"],
            "scenario": scenario,
            "steps": item["steps"],
            "loop_idx": loop_idx,
            "loop_step": loop_step,
            "descriptions": descriptions
        })
        print("  ✓ 成功")
    else:
        print("  ✗ 跳过")

    time.sleep(1)

# 重新编号
for new_id, entry in enumerate(results, start=1):
    entry["id"] = new_id

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)

print(f"\n完成！成功 {len(results)}/{total} 条，结果保存至 {OUTPUT_FILE}")