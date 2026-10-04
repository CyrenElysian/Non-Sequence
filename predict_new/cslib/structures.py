"""script_graph 的结构语义：遍历、校验、边推导、节点审计、深度统计。

本模块是 predict_new 下**唯一**的结构语义实现，所有实验脚本共用它，
避免同一套规则在多个文件里各写一份而产生口径漂移。

规则依据（按优先级）：
  1. prompts/prompt_v*.txt 中的结构定义（模型侧的规范）
  2. CtrlScript 数据的实际约定（已在 v3 的 1076 条上全量验证）

已在 v3 上做过变异测试：注入删边 / 反向边 / 打乱排序 / 分支改非 list 等
已知错误均能被本模块捕获。
"""

from __future__ import annotations

from collections import Counter

VALID_TYPES = ("sequence", "select", "loop", "and_join")
CONTINUE = "continue"


# ---------------------------------------------------------------- 基础遍历

def is_node_id(x) -> bool:
    """节点 id 一律是非负整数的字符串形式（如 "0"、"12"）。"""
    return isinstance(x, str) and x.isdigit()


def branch_values(branches_set):
    """产出 branches_set 的所有分支值（兼容 list / dict / 单节点 id）。

    规范要求分支值是 list；写成 dict 或裸 id 属格式偏离，这里仍能解析，
    以便对偏离数据给出诊断而不是直接崩溃。
    """
    for v in (branches_set or {}).values():
        yield v


def iter_children(node):
    """产出某个结构节点的直接子项（已展平）。非结构节点产出空。"""
    if not isinstance(node, dict):
        return
    t = node.get("type")
    if t == "sequence":
        for e in node.get("script") or []:
            yield e
    elif t == "select":
        for e in node.get("options") or []:
            yield e
    elif t == "and_join":
        for v in branch_values(node.get("branches_set")):
            if isinstance(v, list):
                for e in v:
                    yield e
            else:
                yield v
    elif t == "loop":
        yield node.get("entry")
        for e in node.get("retry") or []:
            yield e
        yield node.get("exit")


def walk_structures(node):
    """深度优先产出所有结构节点（dict 且带合法 type）。"""
    if isinstance(node, list):
        for e in node:
            yield from walk_structures(e)
        return
    if not isinstance(node, dict):
        return
    if node.get("type") in VALID_TYPES:
        yield node
    for e in iter_children(node):
        yield from walk_structures(e)


def collect_nodes(node, out=None):
    """收集所有节点 id（含重数），跳过 "continue" 占位符。

    返回 list，元素可重复 —— 重数信息对节点审计是必要的。
    """
    if out is None:
        out = []
    if is_node_id(node):
        out.append(node)
        return out
    if isinstance(node, list):
        for e in node:
            collect_nodes(e, out)
        return out
    if not isinstance(node, dict):
        return out
    if node.get("type") == "loop":
        if is_node_id(node.get("entry")):
            out.append(node["entry"])
        if is_node_id(node.get("exit")):
            out.append(node["exit"])
        for e in node.get("retry") or []:
            if e != CONTINUE:
                collect_nodes(e, out)
        return out
    for e in iter_children(node):
        if e != CONTINUE:
            collect_nodes(e, out)
    return out


def all_nodes(node) -> set:
    """返回子树中出现的节点 id 集合。"""
    return set(collect_nodes(node))


def loop_related_nodes(sg) -> set:
    """所有 loop 的 entry / exit，以及 **retry 子树内** 的全部节点。

    注意：必须递归收集 retry 内部节点。只取 entry/exit 会把
    "重复发生在 retry 的嵌套结构里" 这类合法数据误判为违规
    （v3 中 id=189/308/716 即属此类）。
    """
    related = set()
    for s in walk_structures(sg):
        if s.get("type") != "loop":
            continue
        if is_node_id(s.get("entry")):
            related.add(s["entry"])
        if is_node_id(s.get("exit")):
            related.add(s["exit"])
        related |= all_nodes(s.get("retry"))
    return related


# ---------------------------------------------------------------- 排序键

def first_node(x):
    """子项的首个节点 id，用于 select.options / and_join 分支的升序校验。

    定义（与 prompt 一致）：
      - 节点 id          -> 自身
      - list（序列）      -> **首元素** 的 first_node（不是最小值）
      - sequence         -> script 首元素
      - select           -> 各 option 的 first_node 取 **最小**（options 互斥无序）
      - and_join         -> 各 branch 的 first_node 取 **最小**（branches 无序）
      - loop             -> entry
    无法解析时返回 None。
    """
    if is_node_id(x):
        return int(x)
    if isinstance(x, list):
        for e in x:
            v = first_node(e)
            if v is not None:
                return v
        return None
    if not isinstance(x, dict):
        return None
    t = x.get("type")
    if t == "sequence":
        for e in x.get("script") or []:
            v = first_node(e)
            if v is not None:
                return v
        return None
    if t == "select":
        vals = [first_node(o) for o in x.get("options") or []]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else None
    if t == "and_join":
        vals = [first_node(v) for v in branch_values(x.get("branches_set"))]
        vals = [v for v in vals if v is not None]
        return min(vals) if vals else None
    if t == "loop":
        e = x.get("entry")
        return int(e) if is_node_id(e) else None
    return None


# ---------------------------------------------------------------- 结构校验

class StructureReport(dict):
    """结构校验结果：errors（硬错误）/ deviations（格式偏离）/ warnings（提示）。"""

    @property
    def ok(self) -> bool:
        return not self["errors"] and not self["deviations"]


def validate(sg) -> StructureReport:
    """校验 script_graph 的 grammar 与排序。

    errors     : 违反结构定义（缺字段、retry 未以 continue 结尾、排序错误等）
    deviations : 能解析但不符合输出规范（如分支值不是 list）
    warnings   : 非致命（多余键、分支名不连续等）
    """
    errors: list[str] = []
    deviations: list[str] = []
    warnings: list[str] = []

    if not isinstance(sg, dict):
        errors.append("script_graph 不是 JSON 对象")
        return StructureReport(errors=errors, deviations=deviations, warnings=warnings)
    if sg.get("type") != "sequence":
        errors.append(f"顶层 type 应为 sequence，实际 {sg.get('type')!r}")

    _validate_node(sg, "$", errors, deviations, warnings)
    return StructureReport(errors=errors, deviations=deviations, warnings=warnings)


def _validate_node(node, path, errors, deviations, warnings):
    if is_node_id(node):
        return
    if isinstance(node, list):
        errors.append(f"{path}: 出现裸露的 list（应为结构对象）")
        return
    if not isinstance(node, dict):
        errors.append(f"{path}: 非法节点类型 {type(node).__name__}")
        return

    t = node.get("type")
    if t not in VALID_TYPES:
        errors.append(f"{path}: 未知或缺失 type={t!r}")
        return

    keys = set(node.keys())

    if t == "sequence":
        if "script" not in node:
            errors.append(f"{path}: sequence 缺少 script")
            return
        if not isinstance(node["script"], list) or not node["script"]:
            errors.append(f"{path}: sequence.script 必须是非空 list")
            return
        if keys - {"type", "script"}:
            warnings.append(f"{path}: sequence 含多余键 {sorted(keys - {'type', 'script'})}")
        for i, e in enumerate(node["script"]):
            _validate_node(e, f"{path}.script[{i}]", errors, deviations, warnings)

    elif t == "select":
        opts = node.get("options")
        if not isinstance(opts, list):
            errors.append(f"{path}: select 缺少 options 或不是 list")
            return
        if len(opts) < 2:
            errors.append(f"{path}: select.options 少于 2 项（{len(opts)}）")
        if keys - {"type", "options"}:
            warnings.append(f"{path}: select 含多余键 {sorted(keys - {'type', 'options'})}")
        fv = [first_node(o) for o in opts]
        if any(v is None for v in fv):
            errors.append(f"{path}: select.options 有无法解析首节点的子项 {fv}")
        elif fv != sorted(fv):
            errors.append(f"{path}: select.options 未按首节点升序 -> {fv}（应为 {sorted(fv)}）")
        for i, o in enumerate(opts):
            _validate_node(o, f"{path}.options[{i}]", errors, deviations, warnings)

    elif t == "and_join":
        bs = node.get("branches_set")
        if not isinstance(bs, dict):
            errors.append(f"{path}: and_join 缺少 branches_set 或不是 dict")
            return
        names = [k for k in bs.keys() if k != "type"]
        if "type" in bs:
            errors.append(f"{path}: branches_set 内不允许出现 type 键")
        if len(names) < 2:
            errors.append(f"{path}: and_join 少于 2 个分支（{len(names)}）")
        if keys - {"type", "branches_set"}:
            warnings.append(f"{path}: and_join 含多余键 {sorted(keys - {'type', 'branches_set'})}")
        bad_names = [n for n in names if not (n.startswith("b") and n[1:].isdigit())]
        if bad_names:
            errors.append(f"{path}: 分支名非法 {bad_names}（应形如 b1、b2）")
        fv = []
        for k in names:
            v = bs[k]
            if not isinstance(v, list):
                deviations.append(
                    f"{path}.{k}: 分支值是 {type(v).__name__}，规范要求 list")
                _validate_node(v, f"{path}.{k}", errors, deviations, warnings)
                fv.append(first_node(v))
                continue
            if not v:
                errors.append(f"{path}.{k}: 分支为空 list")
                continue
            fv.append(first_node(v))
            for i, e in enumerate(v):
                _validate_node(e, f"{path}.{k}[{i}]", errors, deviations, warnings)
        if any(v is None for v in fv):
            errors.append(f"{path}: and_join 有无法解析首节点的分支 {fv}")
        elif fv != sorted(fv):
            errors.append(f"{path}: and_join 分支未按首节点升序 -> {fv}（应为 {sorted(fv)}）")

    elif t == "loop":
        e, x = node.get("entry"), node.get("exit")
        if not is_node_id(e):
            errors.append(f"{path}: loop.entry 非法 {e!r}")
        if not is_node_id(x):
            errors.append(f"{path}: loop.exit 非法 {x!r}")
        retry = node.get("retry")
        if not isinstance(retry, list) or not retry:
            errors.append(f"{path}: loop.retry 必须是非空 list")
        else:
            if retry[-1] != CONTINUE:
                errors.append(f'{path}: loop.retry 末元素必须是 "continue"，实际 {retry[-1]!r}')
            if CONTINUE in retry[:-1]:
                errors.append(f'{path}: loop.retry 中 "continue" 只能出现在末尾')
            for i, r in enumerate(retry):
                if r != CONTINUE:
                    _validate_node(r, f"{path}.retry[{i}]", errors, deviations, warnings)
        if keys - {"type", "entry", "retry", "exit"}:
            warnings.append(
                f"{path}: loop 含多余键 {sorted(keys - {'type', 'entry', 'retry', 'exit'})}")


# ---------------------------------------------------------------- 边推导

class DeriveError(Exception):
    pass


def derive_edges(sg) -> set:
    """由 script_graph 推导出它蕴含的有向边集合 {(src, tgt), ...}。

    语义：
      sequence  相邻元素 exit -> entry 全连接
      select / and_join  前置节点 -> 各分支 entry；各分支 exit -> 合并点
      loop      entry -> retry 首项；retry 链内顺序连接；retry 末项 -> entry（continue）；
                entry -> exit（跳过循环的通道）
    """
    _, _, edges = _derive(sg)
    return edges


def _derive(node):
    """返回 (entry_set, exit_set, internal_edges)。"""
    if is_node_id(node):
        return {node}, {node}, set()
    if isinstance(node, list):
        return _derive({"type": "sequence", "script": node})
    if not isinstance(node, dict):
        raise DeriveError(f"非法节点 {node!r}")

    t = node.get("type")

    if t == "sequence":
        elems = node.get("script") or []
        entries = prev = None
        edges: set = set()
        for e in elems:
            en, ex, ed = _derive(e)
            edges |= ed
            if entries is None:
                entries = en
            if prev is not None:
                edges |= {(a, b) for a in prev for b in en}
            prev = ex
        if entries is None:
            raise DeriveError("空 sequence")
        return entries, prev, edges

    if t in ("select", "and_join"):
        if t == "select":
            children = node.get("options") or []
        else:
            children = []
            for v in branch_values(node.get("branches_set")):
                children.append(v if isinstance(v, list) else [v])
        entries, exits, edges = set(), set(), set()
        for c in children:
            en, ex, ed = _derive(c)
            entries |= en
            exits |= ex
            edges |= ed
        return entries, exits, edges

    if t == "loop":
        e, x = node.get("entry"), node.get("exit")
        if not is_node_id(e) or not is_node_id(x):
            raise DeriveError("loop entry/exit 非法")
        items = [r for r in (node.get("retry") or []) if r != CONTINUE]
        edges: set = set()
        prev = {e}
        for r in items:
            en, ex, ed = _derive(r)
            edges |= ed
            edges |= {(a, b) for a in prev for b in en}
            prev = ex
        if items:
            edges |= {(a, e) for a in prev}   # continue：回到 entry
        edges.add((e, x))                      # 跳过循环
        return {e}, {x}, edges

    raise DeriveError(f"未知 type={t!r}")


def parse_edge_strings(raw):
    """把 ["a->b", ...] 解析为 (集合, 非法项, 重复项)。"""
    pairs, bad, seen, dups = set(), [], set(), []
    for e in raw or []:
        if not isinstance(e, str) or e.count("->") != 1:
            bad.append(e)
            continue
        a, b = e.split("->")
        if not (a.isdigit() and b.isdigit()):
            bad.append(e)
            continue
        if (a, b) in seen:
            dups.append(e)
        seen.add((a, b))
        pairs.add((a, b))
    return pairs, bad, dups


def edge_diff(pred_edges, gold_edges):
    """比较预测边集与金标边集（集合语义）。

    返回 dict：
      missing   金标有、预测缺
      extra     预测有、金标缺
      reversed  extra 中方向恰好相反的（疑似方向错）
      ok        missing 与 extra 均为空
    """
    pred, _, _ = parse_edge_strings(pred_edges)
    gold, _, _ = parse_edge_strings(gold_edges)
    missing = gold - pred
    extra = pred - gold
    reversed_ = {(a, b) for (a, b) in extra if (b, a) in gold}
    return {
        "missing": missing,
        "extra": extra,
        "reversed": reversed_,
        "ok": not missing and not extra,
    }


# ---------------------------------------------------------------- 节点审计

def audit_nodes(gold_node_ids, pred_sg) -> dict:
    """审计预测的 script_graph 是否恰当地使用了给定的节点集合。

    规则：
      - 给定节点必须全部出现（missing 为空）
      - 不得出现未定义节点（extra 为空）
      - 非 loop 记录每个节点恰好一次
      - loop 记录允许重复，但重复节点必须与某个 loop 相关，且不得出现超过 2 次
    """
    gold_ids = set(gold_node_ids)
    used = collect_nodes(pred_sg)
    counts = Counter(used)
    used_set = set(used)

    missing = sorted(gold_ids - used_set, key=_idkey)
    extra = sorted(used_set - gold_ids, key=_idkey)
    duplicated = sorted([k for k, c in counts.items() if c > 1], key=_idkey)
    over = sorted([k for k, c in counts.items() if c > 2], key=_idkey)

    related = loop_related_nodes(pred_sg)
    unexpected_dup = sorted([k for k in duplicated if k not in related], key=_idkey)

    valid = not missing and not extra and not over and not unexpected_dup
    return {
        "nodes_valid": valid,
        "nodes_missing": missing,
        "nodes_extra": extra,
        "nodes_duplicated": duplicated,
        "nodes_over_duplicated": over,
        "nodes_unexpected_dup": unexpected_dup,
    }


def _idkey(x):
    return (0, int(x)) if is_node_id(x) else (1, str(x))


# ---------------------------------------------------------------- 深度与结构计数

def depth_and_type_cnt(sg):
    """计算最大嵌套深度与各结构数量。

    与 introduce/stats/count_structure.py 的口径一致：
      - max_depth：结构节点的最大层级，最外层结构算 1，无结构则为 0
      - type_cnt ：{sequence, select, loop, and_join, total}
    """
    max_depth, counts = _analyze(sg, depth=1)
    out = {t: int(counts.get(t, 0)) for t in VALID_TYPES}
    out["total"] = sum(out.values())
    return max_depth, out


def _analyze(node, depth=1):
    if not isinstance(node, dict):
        return 0, {}
    t = node.get("type")
    counts: dict = {}
    max_depth = 0
    if t in VALID_TYPES:
        counts[t] = 1
        max_depth = depth
        child_depth = depth + 1
    else:
        child_depth = depth
    for child in iter_children(node):
        if child == CONTINUE:
            continue
        sub_depth, sub_counts = _analyze(child, child_depth)
        max_depth = max(max_depth, sub_depth)
        for k, v in sub_counts.items():
            counts[k] = counts.get(k, 0) + v
    return max_depth, counts


def has_nonlinear(type_cnt) -> bool:
    return any(type_cnt.get(t, 0) > 0 for t in ("select", "loop", "and_join"))


def nonlinearity_label(type_cnt) -> str:
    return "nonlinear" if has_nonlinear(type_cnt) else "linear"


def combo_label(type_cnt) -> str:
    """互斥细分，保留组合名（如 and_join+loop）。"""
    present = sorted(t for t in ("select", "loop", "and_join") if type_cnt.get(t, 0) > 0)
    return "+".join(present) if present else "sequence"


def purity_label(type_cnt) -> str:
    """互斥细分，两种及以上非线性统一为 mixed。"""
    present = [t for t in ("select", "loop", "and_join") if type_cnt.get(t, 0) > 0]
    if not present:
        return "linear"
    return "mixed" if len(present) >= 2 else present[0]
