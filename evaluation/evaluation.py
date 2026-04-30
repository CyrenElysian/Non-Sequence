import json
import random
from collections import Counter

def has_complex_structure(sg):
    """递归检查 script_graph 是否包含 select/loop/and_join"""
    if isinstance(sg, str):
        return False
    if isinstance(sg, dict):
        if sg.get("type") in ("select", "loop", "and_join"):
            return True
        for v in sg.values():
            if has_complex_structure(v):
                return True
    elif isinstance(sg, list):
        for item in sg:
            if has_complex_structure(item):
                return True
    return False

def classify_errors(gen_edges, ref_edges, nodes_valid, sg_match):
    """对预测错误进行分类，返回标签集合"""
    errors = []
    gen_set = set(gen_edges) if gen_edges else set()
    ref_set = set(ref_edges) if ref_edges else set()
    extra = gen_set - ref_set
    missing = ref_set - gen_set
    if extra:
        errors.append("extra_edges")
    if missing:
        errors.append("missing_edges")
    if not sg_match:
        errors.append("sg_mismatch")
    if not nodes_valid:
        errors.append("invalid_nodes")
    return errors

def weighted_sample(pool, target):
    """从 pool 中随机抽取 target 个样本，不足则全取"""
    if len(pool) <= target:
        return pool.copy()
    return random.sample(pool, target)

def stratified_sampling(results, total_count, target_ratio=0.1):
    # 分层
    complex_correct = []
    complex_error = []
    simple_correct = []
    simple_error = []

    for r in results:
        complex = has_complex_structure(r["reference_sg"])
        is_correct = r["edges_match"] and r["sg_match"]
        if complex:
            if is_correct:
                complex_correct.append(r)
            else:
                complex_error.append(r)
        else:
            if is_correct:
                simple_correct.append(r)
            else:
                simple_error.append(r)

    stats = {
        "total": total_count,
        "complex_correct": len(complex_correct),
        "complex_error": len(complex_error),
        "simple_correct": len(simple_correct),
        "simple_error": len(simple_error)
    }

    # 目标样本数
    target_samples = int(total_count * target_ratio)

    # 各层权重 (优先级：复杂错误 > 简单错误 > 复杂正确 > 简单正确)
    weights = {
        "complex_error": 5,
        "simple_error": 3,
        "complex_correct": 2,
        "simple_correct": 1
    }
    total_weight = sum(weights.values())

    # 理论配额
    quotas = {
        "complex_error": min(len(complex_error), target_samples * weights["complex_error"] // total_weight),
        "simple_error": min(len(simple_error), target_samples * weights["simple_error"] // total_weight),
        "complex_correct": min(len(complex_correct), target_samples * weights["complex_correct"] // total_weight),
        "simple_correct": min(len(simple_correct), target_samples * weights["simple_correct"] // total_weight)
    }

    # 剩余名额（因为取整或容量不足）
    assigned = sum(quotas.values())
    remaining = target_samples - assigned

    # 若还有剩余名额，按权重顺序补足，每层一次补1个，直到用完或满
    layer_order = ["complex_error", "simple_error", "complex_correct", "simple_correct"]
    while remaining > 0:
        added = False
        for layer in layer_order:
            max_quota = len(eval(layer))  # 用变量名获取实际列表长度不好，后面用字典
            if quotas[layer] < max_quota:
                quotas[layer] += 1
                remaining -= 1
                added = True
                if remaining == 0:
                    break
        if not added:  # 所有层都满了，退出
            break

    # 正式抽样
    sample_set = []
    sample_set.extend(weighted_sample(complex_error, quotas["complex_error"]))
    sample_set.extend(weighted_sample(simple_error, quotas["simple_error"]))
    sample_set.extend(weighted_sample(complex_correct, quotas["complex_correct"]))
    sample_set.extend(weighted_sample(simple_correct, quotas["simple_correct"]))

    # 为样本打上错误标签
    for item in sample_set:
        if not (item["edges_match"] and item["sg_match"]):
            item["error_tags"] = classify_errors(
                item.get("generated_edges", []),
                item["reference_edges"],
                item["nodes_valid"],
                item["sg_match"]
            )
        else:
            item["error_tags"] = []

    return sample_set, stats

if __name__ == "__main__":
    with open("../predict/evaluation_results.json", "r", encoding="utf-8") as f:
        results = json.load(f)

    total = len(results)
    print(f"总数据量: {total}")
    random.seed(42)

    sampled, layer_stats = stratified_sampling(results, total, target_ratio=0.1)

    with open("sampling_review.json", "w", encoding="utf-8") as f:
        json.dump(sampled, f, ensure_ascii=False, indent=2)

    print("\n各层数据量:")
    for k, v in layer_stats.items():
        print(f"  {k}: {v}")
    print(f"实际抽样数量: {len(sampled)} ({len(sampled) / total * 100:.1f}%)")

    error_counter = Counter()
    for item in sampled:
        for tag in item.get("error_tags", []):
            error_counter[tag] += 1
    print("\n抽样样本错误类型分布:")
    for tag, count in error_counter.most_common():
        print(f"  {tag}: {count}")