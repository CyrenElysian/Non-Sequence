"""落盘工具：原子写入、损坏容忍、run manifest。

修正旧脚本的问题：
  - 旧版每条记录都**全量重写**整个 checkpoint（O(n²) I/O），且是**非原子写**；
    一旦中断就会留下截断的 JSON，而 `load_checkpoint` 没有异常保护，
    之后每次启动都崩在加载阶段。
  这里：原子写（临时文件 + os.replace）+ 自动保留 .bak + 损坏时回退备份。
"""

from __future__ import annotations

import json
import os
import shutil


def atomic_write_json(obj, path, indent=2, keep_backup=True):
    """原子写入 JSON：先写临时文件并 fsync，再 os.replace 覆盖目标。"""
    path = os.path.abspath(path)
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    tmp = f"{path}.tmp.{os.getpid()}"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=indent)
        f.flush()
        os.fsync(f.fileno())

    if keep_backup and os.path.exists(path):
        try:
            shutil.copy2(path, path + ".bak")
        except OSError:
            pass
    os.replace(tmp, path)
    return path


def load_json_safe(path, allow_backup=True):
    """读取 JSON；文件不存在返回 None；损坏时回退到 .bak，否则抛出清晰错误。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError as exc:
        if allow_backup and os.path.exists(path + ".bak"):
            try:
                with open(path + ".bak", "r", encoding="utf-8") as f:
                    data = json.load(f)
                print(f"[!] {path} 损坏，已回退到备份 {os.path.basename(path)}.bak")
                return data
            except (OSError, json.JSONDecodeError):
                pass
        raise RuntimeError(
            f"文件已损坏且无可用备份：{path}\n  原因：{exc}\n"
            f"  处理：删除该文件重新开始，或从 .bak 手工恢复。"
        ) from exc


def save_manifest(meta, path):
    """写入/更新 run manifest（记录模型、提示词、金标指纹等实验溯源信息）。"""
    import datetime

    path = os.path.abspath(path)
    old = load_json_safe(path) or {}
    old.update(meta)
    old.setdefault("created_at", datetime.datetime.now().isoformat(timespec="seconds"))
    old["updated_at"] = datetime.datetime.now().isoformat(timespec="seconds")
    atomic_write_json(old, path)
    return path
