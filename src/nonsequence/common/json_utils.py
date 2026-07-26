"""Shared JSON loading, parsing, and atomic checkpoint utilities."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any


def load_json(path: Path) -> Any:
    """Load UTF-8 JSON from *path*."""
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def atomic_write_json(path: Path, data: Any, *, indent: int | None = 2) -> None:
    """Write UTF-8 JSON atomically, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temp_name = handle.name
            json.dump(data, handle, ensure_ascii=False, indent=indent)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except OSError:
        if temp_name is not None:
            Path(temp_name).unlink(missing_ok=True)
        raise


def parse_fenced_json(content: str) -> Any:
    """Parse JSON from plain text or a Markdown fenced code block."""
    text = content.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.IGNORECASE | re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError as direct_error:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\[{]", text):
            try:
                value, _ = decoder.raw_decode(text[match.start() :])
                return value
            except json.JSONDecodeError:
                continue
        raise ValueError("Response does not contain valid JSON") from direct_error


def load_checkpoint(path: Path, default: Any) -> Any:
    """Load a checkpoint, returning *default* when it is absent or invalid."""
    if not path.exists():
        return default
    try:
        return load_json(path)
    except (OSError, json.JSONDecodeError):
        return default
