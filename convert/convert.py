# 将proscript_simple中的数据转化为我们所定义的JSON结构

import json
from collections import defaultdict, deque

def load_data(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def build_graph(edges, num_nodes):
    adj = defaultdict(list)
    in_degree = [0] * num_nodes
    for edge in edges:
        u, v = map(int, edge.split('->'))
        adj[u].append(v)
        in_degree[v] += 1
    return adj, in_degree

def topological_sort(adj, in_degree, num_nodes):
    indeg = in_degree[:]
    q = deque([i for i in range(num_nodes) if indeg[i] == 0])
    order = []
    while q:
        u = q.popleft()
        order.append(u)
        for v in adj[u]:
            indeg[v] -= 1
            if indeg[v] == 0:
                q.append(v)
    return order

def find_and_join_structure(adj, in_degree, num_nodes):
    forks = [i for i in range(num_nodes) if len(adj[i]) >= 2]
    joins = [i for i in range(num_nodes) if in_degree[i] >= 2]
    and_joins = {}  # key: join_node, value: (fork_node, list of branches)

    for s in forks:
        for t in joins:
            if s == t:
                continue
            # 从 s 的每一个直接后继出发，尝试走到 t
            branches = []
            valid = True
            visited_global = set()
            for start in adj[s]:
                branch = []
                cur = start
                while cur != t:
                    if cur in visited_global:  # 分支出现交叉，不符合要求
                        valid = False
                        break
                    visited_global.add(cur)
                    branch.append(cur)
                    # AND‑JOIN 要求分支内部是线性链，即每个中间节点出度必须为 1
                    if len(adj[cur]) != 1:
                        valid = False
                        break
                    cur = adj[cur][0]
                if not valid:
                    break
                branches.append(branch)
            if not valid or len(branches) < 2:
                continue

            # 检查 t 的所有前驱是否恰好是每个分支的最后一个节点（或 s 本身，若分支为空）
            t_incoming = set()
            for u in range(num_nodes):
                if t in adj[u]:
                    t_incoming.add(u)
            expected_incoming = set()
            for branch in branches:
                if branch:
                    expected_incoming.add(branch[-1])
                else:
                    expected_incoming.add(s)
            if t_incoming == expected_incoming:
                and_joins[t] = (s, branches)
    return and_joins

def build_script_graph(adj, in_degree, num_nodes, and_joins):
    topo = topological_sort(adj, in_degree, num_nodes)
    covered = [False] * num_nodes
    script = []

    i = 0
    while i < len(topo):
        node = topo[i]
        if covered[node]:
            i += 1
            continue

        # 检查当前节点是否为某个 AND‑JOIN 的分叉点
        fork_of = None
        for t, (s, branches) in and_joins.items():
            if s == node:
                fork_of = (s, t, branches)
                break

        if fork_of:
            s, t, branches = fork_of
            # ① 将分叉点 s 加入主脚本
            script.append(str(s))
            covered[s] = True

            # ② 构建 and_join 对象，并将分支内节点全部标记为 covered
            branches_dict = {}
            for idx, branch in enumerate(branches, start=1):
                branches_dict[f"b{idx}"] = [str(n) for n in branch]
                for n in branch:
                    covered[n] = True
            script.append({
                "type": "and_join",
                "branches_set": branches_dict
            })
            # 注意：汇合点 t 尚未加入，也不应标记 covered，因为它还属于主序列
            i += 1
            continue

        # 普通节点
        if not covered[node]:
            script.append(str(node))
            covered[node] = True
        i += 1

    # 包装成 sequence 类型
    return {
        "type": "sequence",
        "script": script
    }

def convert_scenario(item, idx):
    scenario = item["scenario"]
    # 保留原始 events 字典作为 unordered_nodes（键已为字符串）
    unordered_nodes = item["events"]
    num_nodes = len(unordered_nodes)
    edges = item["gold_edges_for_prediction"]

    adj, in_degree = build_graph(edges, num_nodes)
    and_joins = find_and_join_structure(adj, in_degree, num_nodes)
    script_graph = build_script_graph(adj, in_degree, num_nodes, and_joins)

    new_item = {
        "id": idx + 1,
        "scenario": scenario,
        "unordered_nodes": unordered_nodes,
        "edges": edges,
        "script_graph": script_graph
    }
    return new_item

def main():
    input_file = "../proscript_simple/dev.json"
    output_file = "converted_dev.json"

    data = load_data(input_file)
    converted = []
    for i, item in enumerate(data):
        try:
            conv = convert_scenario(item, i)
            converted.append(conv)
        except Exception as e:
            print(f"Error converting item {i} (scenario: {item.get('scenario', 'unknown')}): {e}")

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(converted, f, indent=2, ensure_ascii=False)

    print(f"Conversion complete. Output written to {output_file}")

if __name__ == "__main__":
    main()