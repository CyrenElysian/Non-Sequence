"""模型调用与响应解析。

修正旧脚本的三个问题：
  1. **加入重试与指数退避**。旧 predict.py 完全没有重试，一次 429/超时就会
     变成永久失败记录并计入分母，使指标受网络运气影响。
  2. **JSON 解析加兜底**。旧版只做代码围栏剥离，遇到前导文字或多对象输出
     直接失败；这里补上括号配平扫描，并对候选对象做字段过滤。
  3. **记录提示词指纹**，让结果文件具备实验溯源能力（旧版没有任何来源信息）。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import time
import uuid


# ---------------------------------------------------------------- 客户端

def build_headers(base_url: str, extra=None):
    """构造 OpenAI 客户端的 default_headers。

    opencode 网关（https://opencode.ai/zen/go/v1）**强制要求** x-opencode-session
    请求头，缺失会返回 400 MissingSessionID。原 introduce.py / check.py 就是
    无条件带上一个 uuid4 会话 id（见其 `default_headers={...}`）。
    这里对含 "opencode" 的 base_url 自动补上，并允许用 --header 覆盖或追加。

    extra: ["Name=Value", ...]
    """
    headers = {}
    for item in extra or []:
        if "=" not in item:
            raise ValueError(f"--header 需要 NAME=VALUE 形式，收到：{item!r}")
        name, value = item.split("=", 1)
        headers[name.strip()] = value.strip()

    if "opencode" in (base_url or "").lower() and "x-opencode-session" not in headers:
        headers["x-opencode-session"] = str(uuid.uuid4())
    return headers


def get_client(base_url: str, api_key_env: str = "DEEPSEEK_API_KEY", headers=None):
    """惰性创建 OpenAI 客户端（不在模块顶层创建，便于离线 import）。

    headers 会作为 default_headers 传入；opencode 网关的会话头由 build_headers 处理。
    """
    from openai import OpenAI

    key = os.getenv(api_key_env)
    if not key:
        raise RuntimeError(
            f"环境变量 {api_key_env} 未设置，无法调用模型。"
            f"（可用 --api-key-env 指定其它变量名）")
    kwargs = {"api_key": key, "base_url": base_url}
    if headers:
        kwargs["default_headers"] = headers
    return OpenAI(**kwargs)


# ---------------------------------------------------------------- 调用

def call_model(client, model, system_prompt, user_message, *,
               reasoning_effort="high", enable_thinking=True,
               max_retries=3, retry_delay=2.0, max_tokens=None, verbose=True):
    """调用模型，失败按指数退避重试；全部失败后抛出最后一个异常。"""
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "stream": False,
            }
            if reasoning_effort:
                kwargs["reasoning_effort"] = reasoning_effort
            if enable_thinking:
                kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            resp = client.chat.completions.create(**kwargs)
            return resp.choices[0].message.content
        except Exception as exc:                      # noqa: BLE001
            last_exc = exc
            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt) + random.uniform(0, 1.0)
                if verbose:
                    print(f"      [retry {attempt + 1}/{max_retries}] {type(exc).__name__}: "
                          f"{str(exc)[:120]}  -> {delay:.1f}s 后重试")
                time.sleep(delay)
    raise last_exc


# ---------------------------------------------------------------- 解析

def _balanced_objects(text):
    """扫描文本中所有**顶层**配平的花括号对象（字符串感知）。"""
    objs, i, n = [], 0, len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth, j, in_str, esc = 0, i, False, False
        while j < n:
            ch = text[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            j += 1
        objs.append(text[i:j + 1])
        i = j + 1
    return objs


def extract_json(text):
    """从模型输出中提取 JSON 对象。

    依次尝试：代码围栏内容 -> 整体解析 -> 配平扫描出的候选对象
    （优先含 edges + script_graph 的）。
    """
    if not isinstance(text, str):
        raise ValueError("模型输出不是字符串")

    candidates = []
    if "```json" in text:
        candidates.append(text.split("```json", 1)[1].split("```", 1)[0].strip())
    if "```" in text:
        candidates.append(text.split("```", 1)[1].split("```", 1)[0].strip())
    candidates.append(text.strip())
    candidates.extend(_balanced_objects(text))

    # 优先返回同时含 edges 与 script_graph 的对象
    parsed = []
    for c in candidates:
        if not c:
            continue
        try:
            obj = json.loads(c)
        except Exception:                             # noqa: BLE001
            continue
        if isinstance(obj, dict):
            parsed.append(obj)

    for obj in parsed:
        if "edges" in obj and "script_graph" in obj:
            return obj
    if parsed:
        return parsed[0]
    raise ValueError("未能从模型输出中解析出 JSON 对象")


# ---------------------------------------------------------------- 指纹

def prompt_meta(path):
    """提示词溯源信息：路径、sha256 前 16 位、字符数。"""
    path = os.path.abspath(path)
    with open(path, "rb") as f:
        raw = f.read()
    return {
        "file": path,
        "name": os.path.basename(path),
        "sha256_16": hashlib.sha256(raw).hexdigest()[:16],
        "chars": len(raw.decode("utf-8", errors="replace")),
    }
