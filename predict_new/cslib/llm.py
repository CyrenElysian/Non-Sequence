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

class FatalAPIError(RuntimeError):
    """重试也无法恢复的错误（配额耗尽 / 认证失败），应立即终止本次运行。

    配额耗尽时若继续逐条重试，会白白消耗大量时间，并把大批"空预测"
    失败记录写进 checkpoint（续跑时会被当成已完成而跳过）。
    """


_QUOTA_HINTS = ("insufficient", "quota", "balance", "credit", "billing",
                "exceeded your current", "no available", "out of budget")

# 400 类错误里，这些关键词表示「该模型不支持这个参数/协议」——重试永远不会成功
_UNSUPPORTED_HINTS = ("modelprotocolunsupported", "does not support", "not supported",
                      "unsupported", "unknown parameter", "unrecognized",
                      "invalid parameter", "not allowed")


def is_fatal_api_error(exc) -> bool:
    """判断异常是否属于「重试也没用」的类型。

    原则：除 408（超时）与 429（限流，可能瞬时恢复）外，所有 4xx 都是
    客户端请求本身有问题——重试同样的请求不会有不同结果，一律视为致命。
    这能避免"模型不支持某参数"时对上千条样本逐一空转重试。
    """
    if type(exc).__name__ in ("AuthenticationError", "PermissionDeniedError"):
        return True
    status = getattr(exc, "status_code", None)
    if status is not None:
        if 400 <= status < 500 and status not in (408, 429):
            return True
        if status == 429 and any(h in str(exc).lower() for h in _QUOTA_HINTS):
            return True
        return False
    msg = str(exc).lower()
    if any(h in msg for h in _QUOTA_HINTS):
        return True
    if any(h in msg for h in _UNSUPPORTED_HINTS):
        return True
    return False


def _responses_text(resp) -> str:
    """从 Responses API 的返回对象里取出文本。"""
    txt = getattr(resp, "output_text", None)
    if txt:
        return txt
    parts = []
    for item in getattr(resp, "output", None) or []:
        for block in getattr(item, "content", None) or []:
            t = getattr(block, "text", None)
            if t:
                parts.append(t)
    return "\n".join(parts)


def _is_protocol_error(exc) -> bool:
    """是否是「该模型不支持这个协议」——需要换另一种协议。"""
    if getattr(exc, "status_code", None) != 400:
        return False
    m = str(exc).lower()
    return "protocol" in m or "modelprotocolunsupported" in m


def call_model(client, model, system_prompt, user_message, *,
               reasoning_effort="high", enable_thinking=True, protocol="chat",
               max_retries=3, retry_delay=2.0, max_tokens=None, verbose=True):
    """调用模型，失败按指数退避重试；全部失败后抛出最后一个异常。

    protocol:
      "chat"      走 chat.completions（glm / deepseek 等）
      "responses" 走 Responses API（grok 系列只支持这一种）

    配额耗尽 / 认证失败 / 参数不被支持等不可恢复错误会立即抛 FatalAPIError，
    不做无谓重试——否则会对上千条样本逐一空转。
    """
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            if protocol == "responses":
                kwargs = {"model": model, "instructions": system_prompt,
                          "input": user_message}
                if reasoning_effort:
                    kwargs["reasoning"] = {"effort": reasoning_effort}
                if max_tokens:
                    kwargs["max_output_tokens"] = max_tokens
                return _responses_text(client.responses.create(**kwargs))

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
            if is_fatal_api_error(exc):
                raise FatalAPIError(f"{type(exc).__name__}: {str(exc)[:500]}") from exc
            if attempt < max_retries:
                delay = retry_delay * (2 ** attempt) + random.uniform(0, 1.0)
                if verbose:
                    print(f"      [retry {attempt + 1}/{max_retries}] {type(exc).__name__}: "
                          f"{str(exc)[:120]}  -> {delay:.1f}s 后重试")
                time.sleep(delay)
    raise last_exc


def _is_connection_error(exc) -> bool:
    """网络层错误（可重试），区别于"模型不支持"这类确定性错误。"""
    if type(exc).__name__ in ("APIConnectionError", "APITimeoutError",
                              "ConnectError", "ReadTimeout", "ConnectTimeout"):
        return True
    m = str(exc).lower()
    return "connection error" in m or "timed out" in m or "connection reset" in m


def detect_protocol(client, model, verbose=True, attempts=3):
    """探测某模型能用哪种协议，返回 "chat" / "responses" / None。

    在跑全量之前探测一次，避免每条样本都试错（那会把请求数翻倍）。
    网络抖动会重试，只有确定性的"协议不支持"才会切换到另一种协议；
    网络层最终失败会明确报出来，不会误判成"模型不可用"。
    """
    probe = "Reply with exactly: ok"
    chat_err = _try_protocol(client, "chat", model, probe, attempts)
    if chat_err is None:
        if verbose:
            print(f"  [协议探测] {model} -> chat.completions 可用")
        return "chat"

    if not _is_protocol_error(chat_err) and _is_connection_error(chat_err):
        if verbose:
            print(f"  [协议探测] 网络连接失败（重试 {attempts} 次后仍失败）：")
            print(f"             {str(chat_err)[:200]}")
            print(f"             这属于网络/网关问题，不是模型或协议问题；请稍后重试。")
        return None

    resp_err = _try_protocol(client, "responses", model, probe, attempts)
    if resp_err is None:
        if verbose:
            print(f"  [协议探测] {model} -> responses 可用"
                  f"（chat 被拒：{str(chat_err)[:100]}）")
        return "responses"

    if verbose:
        print(f"  [协议探测] {model} 两种协议都不可用：")
        print(f"             chat      : {str(chat_err)[:160]}")
        print(f"             responses : {str(resp_err)[:160]}")
        if _is_connection_error(resp_err):
            print(f"             注意：含网络层错误，可能是网关临时不可用，建议稍后重试。")
    return None


def _try_protocol(client, protocol, model, probe, attempts):
    """按协议发一次极小请求；返回 None 表示成功，否则返回最后一个异常。"""
    last = None
    for i in range(attempts):
        try:
            if protocol == "chat":
                client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": probe}],
                    stream=False)
            else:
                client.responses.create(model=model, input=probe)
            return None
        except Exception as exc:                      # noqa: BLE001
            last = exc
            if not _is_connection_error(exc):
                return exc                            # 确定性错误，无需重试
            time.sleep(1.5 * (i + 1))
    return last


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
