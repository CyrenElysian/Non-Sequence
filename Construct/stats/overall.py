import json
from collections import Counter

def parse_edges(edges):
    out_deg = {}
    in_deg = {}
    for e in edges:
        a, b = e.split('->')
        out_deg[a] = out_deg.get(a, 0) + 1
        in_deg[b] = in_deg.get(b, 0) + 1
    return out_deg, in_deg

def classify(tc):
    s = tc.get('select', 0)
    l = tc.get('loop', 0)
    aj = tc.get('and_join', 0)
    if s == 0 and l == 0 and aj == 0:
        return 'linear'
    non_zero = (s > 0) + (l > 0) + (aj > 0)
    if non_zero == 1:
        if s > 0:
            return 'only_select'
        elif l > 0:
            return 'only_loop'
        else:
            return 'only_and_join'
    else:
        return 'mixed'

def compute_stats(items):
    count = len(items)
    if count == 0:
        return {'count': 0, 'avg_nodes': 0, 'avg_edges': 0, 'avg_deg': 0}
    total_nodes = sum(item['nodes'] for item in items)
    total_edges = sum(item['edges'] for item in items)
    avg_deg_avg = sum(item['avg_deg'] for item in items) / count
    return {
        'count': count,
        'avg_nodes': total_nodes / count,
        'avg_edges': total_edges / count,
        'avg_deg': avg_deg_avg
    }

def main():
    with open('CtrlScript_with_stats.json', 'r', encoding='utf-8') as f:
        data = json.load(f)

    categories = {
        'linear': [],
        'only_select': [],
        'only_loop': [],
        'only_and_join': [],
        'mixed': []
    }
    overall = []
    high_degree_count = 0

    # 新增：非线性数据统计
    nonlinear_items = []
    nonlinear_high_degree_count = 0
    nonlinear_max_deg_counter = Counter()

    max_deg_counter = Counter()

    for item in data:
        nodes = len(item['unordered_nodes'])
        edges = len(item['edges'])
        out_deg, in_deg = parse_edges(item['edges'])
        avg_deg = edges / nodes if nodes > 0 else 0.0

        all_nodes = set(out_deg.keys()) | set(in_deg.keys())
        max_deg = 0
        for node in all_nodes:
            deg = out_deg.get(node, 0) + in_deg.get(node, 0)
            if deg > max_deg:
                max_deg = deg
        if max_deg == 0 and edges == 0:
            max_deg = 0
        max_deg_counter[max_deg] += 1

        has_high_deg = any(v > 1 for v in out_deg.values()) or any(v > 1 for v in in_deg.values())
        if has_high_deg:
            high_degree_count += 1

        cat = classify(item['type_cnt'])
        categories[cat].append({
            'nodes': nodes,
            'edges': edges,
            'avg_deg': avg_deg
        })
        overall.append({
            'nodes': nodes,
            'edges': edges,
            'avg_deg': avg_deg
        })

        # 非线性数据收集
        if cat != 'linear':
            nonlinear_items.append({
                'nodes': nodes,
                'edges': edges,
                'avg_deg': avg_deg
            })
            nonlinear_max_deg_counter[max_deg] += 1
            if has_high_deg:
                nonlinear_high_degree_count += 1

    # 输出各分类统计
    print("===== 分类统计 =====")
    for cat in categories:
        stats = compute_stats(categories[cat])
        print(f"{cat}:")
        print(f"  计数: {stats['count']}")
        print(f"  平均节点数: {stats['avg_nodes']:.2f}")
        print(f"  平均边数: {stats['avg_edges']:.2f}")
        print(f"  平均出入度: {stats['avg_deg']:.2f}\n")

    # 总体统计
    overall_stats = compute_stats(overall)
    print("===== 总体统计 =====")
    print(f"总数据量: {overall_stats['count']}")
    print(f"平均节点数: {overall_stats['avg_nodes']:.2f}")
    print(f"平均边数: {overall_stats['avg_edges']:.2f}")
    print(f"平均出入度: {overall_stats['avg_deg']:.2f}")
    print(f"含有入度或出度大于1的图的数量: {high_degree_count}")

    print("\n===== 最大度数（出度+入度）分布（整体） =====")
    for k in sorted(max_deg_counter.keys()):
        print(f"最大度数 = {k}: {max_deg_counter[k]} 个")

    # 非线性统计
    nonlinear_stats = compute_stats(nonlinear_items)
    print("\n===== 非线性统计（所有非 linear 的条目） =====")
    print(f"非线性计数: {nonlinear_stats['count']}")
    print(f"平均节点数: {nonlinear_stats['avg_nodes']:.2f}")
    print(f"平均边数: {nonlinear_stats['avg_edges']:.2f}")
    print(f"平均出入度: {nonlinear_stats['avg_deg']:.2f}")
    print(f"含有入度或出度大于1的图的数量（非线性中）: {nonlinear_high_degree_count}")

    print("\n===== 最大度数（出度+入度）分布（非线性） =====")
    for k in sorted(nonlinear_max_deg_counter.keys()):
        print(f"最大度数 = {k}: {nonlinear_max_deg_counter[k]} 个")

if __name__ == '__main__':
    main()