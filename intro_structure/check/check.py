import os
import json
import time
from openai import OpenAI

# -------------------- 配置 --------------------
MODEL = "deepseek-v4-pro"  # DeepSeek V4 Pro 模型
BASE_URL = "https://api.deepseek.com"
BATCH_SIZE = 3  # 每批处理的数据条数（检查任务建议保持较小，确保精度）
OVERWRITE_CHECKPOINT = False  # 是否从头开始（False 则从断点续传）
MAX_RETRIES = 3  # 单批最大重试次数
RETRY_DELAY = 10  # 重试间隔（秒）
BATCH_DELAY = 2  # 批次间延迟（秒）

# 从环境变量读取 API Key
API_KEY = os.getenv("DEEPSEEK_API_KEY")
if not API_KEY:
    raise ValueError("请先设置环境变量 DEEPSEEK_API_KEY")

# -------------------- 文件路径 --------------------
PROMPT_FILE = "check_prompt.txt"  # 质量检查提示词文件
INPUT_FILE = "../processed_data.json"  # 待检查的输入数据
OUTPUT_DATA = "processed_data_check.json"  # 修正后的输出数据
OUTPUT_LOG = "change_log.json"  # 变更日志
CHECKPOINT_FILE = "checkpoint_check.json"  # 检查任务断点文件

# -------------------- 读取提示词 --------------------
with open(PROMPT_FILE, "r", encoding="utf-8") as f:
    system_prompt = f.read()

# 追加输出格式强制要求（确保模型严格遵循）
system_prompt += (
    "\n\nIMPORTANT: You must output the result as a JSON object enclosed in a ```json code block. "
    "The JSON must contain exactly two fields: \"processed_data\" (array) and \"change_log\" (array). "
    "Do not include any other text outside the code block."
)

# -------------------- 读取待检查数据集 --------------------
with open(INPUT_FILE, "r", encoding="utf-8") as f:
    dataset = json.load(f)

total_items = len(dataset)
batches = [dataset[i:i + BATCH_SIZE] for i in range(0, total_items, BATCH_SIZE)]
print(f"数据共 {total_items} 条，分为 {len(batches)} 个批次（每批 {BATCH_SIZE} 条）")

# -------------------- 断点续传初始化 --------------------
if OVERWRITE_CHECKPOINT or not os.path.exists(CHECKPOINT_FILE):
    processed_data = []
    change_log = []
    start_batch = 0
    print("全新运行，未发现断点文件。")
else:
    try:
        with open(CHECKPOINT_FILE, "r", encoding="utf-8") as f:
            ckpt = json.load(f)
        processed_data = ckpt["processed_data"]
        change_log = ckpt["change_log"]
        start_batch = ckpt["next_batch_index"]
        print(f"从断点恢复，已完成 {start_batch} 批，当前已处理 {len(processed_data)} 条数据。")
    except (json.JSONDecodeError, KeyError):
        print("断点文件损坏，将重新开始处理。")
        processed_data = []
        change_log = []
        start_batch = 0

# -------------------- 初始化 OpenAI 客户端 --------------------
client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# -------------------- 逐批处理 --------------------
for idx in range(start_batch, len(batches)):
    batch = batches[idx]
    print(f"\n正在处理第 {idx + 1}/{len(batches)} 批，包含 {len(batch)} 条数据...")

    user_message = (
        f"Here is a batch of data to inspect and correct:\n"
        f"```json\n{json.dumps(batch, ensure_ascii=False, indent=2)}\n```\n"
        f"Please inspect every item strictly according to the rules, correct all issues, "
        f"and output the result exactly as specified."
    )

    success = False
    for attempt in range(MAX_RETRIES):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                stream=False
            )

            answer = response.choices[0].message.content.strip()
            print("API 返回成功，正在解析 JSON...")

            # 提取 JSON
            json_str = answer
            if "```json" in answer:
                json_str = answer.split("```json")[1].split("```")[0].strip()
            elif "```" in answer:
                json_str = answer.split("```")[1].split("```")[0].strip()

            result = json.loads(json_str)

            if "processed_data" not in result or "change_log" not in result:
                raise ValueError("返回结果缺少 processed_data 或 change_log 字段")

            # # 校验：本批次的所有 id 必须都出现在 processed_data 中（禁止删除）
            # batch_ids = {item["id"] for item in batch}
            # returned_ids = {item["id"] for item in result["processed_data"]}
            # missing_ids = batch_ids - returned_ids
            # if missing_ids:
            #     raise ValueError(f"检测到数据被删除，缺失 id: {missing_ids}。禁止删除任何数据！")

            # 收集本批结果
            processed_data.extend(result["processed_data"])
            change_log.extend(result["change_log"])

            # 更新断点文件
            with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "processed_data": processed_data,
                    "change_log": change_log,
                    "next_batch_index": idx + 1
                }, f, ensure_ascii=False, indent=2)

            print(f"✓ 第 {idx + 1} 批完成，累计已处理 {len(processed_data)} 条，生成日志 {len(change_log)} 条。")
            success = True
            break

        except json.JSONDecodeError as e:
            print(f"  JSON 解析失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
        except ValueError as e:
            print(f"  数据校验失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)
        except Exception as e:
            print(f"  API 调用失败 (尝试 {attempt + 1}/{MAX_RETRIES}): {e}")
            if attempt < MAX_RETRIES - 1:
                print(f"  等待 {RETRY_DELAY} 秒后重试...")
                time.sleep(RETRY_DELAY)

    if not success:
        # 保存当前断点，方便人工检查
        with open(CHECKPOINT_FILE, "w", encoding="utf-8") as f:
            json.dump({
                "processed_data": processed_data,
                "change_log": change_log,
                "next_batch_index": idx  # 保留当前失败批次的索引，下次重试
            }, f, ensure_ascii=False, indent=2)
        print(f"✗ 第 {idx + 1} 批处理失败（已重试 {MAX_RETRIES} 次），断点已保存。脚本停止。")
        break

    # 批次间延迟，避免触发频率限制
    time.sleep(BATCH_DELAY)

# -------------------- 最终写入 --------------------
if processed_data:
    with open(OUTPUT_DATA, "w", encoding="utf-8") as f:
        json.dump(processed_data, f, ensure_ascii=False, indent=2)
    print(f"\n处理完成！共保留 {len(processed_data)} 条数据，已保存至 {OUTPUT_DATA}")
else:
    print("警告：没有任何数据被处理，processed_data 为空。")

if change_log:
    with open(OUTPUT_LOG, "w", encoding="utf-8") as f:
        json.dump(change_log, f, ensure_ascii=False, indent=2)
    print(f"修改日志包含 {len(change_log)} 条，已保存至 {OUTPUT_LOG}")
else:
    print("修改日志为空，可能并未进行任何更改。")