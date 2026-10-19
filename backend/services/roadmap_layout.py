"""Top-down developmental lanes that leave user groups and locked nodes intact."""

import math
from collections import defaultdict
from copy import deepcopy


def arrange_tree(board, papers, heights, topic_id, branches):
    result = deepcopy(board)
    nodes = {node["id"]: node for node in result["nodes"]}
    branch_by_paper = {node["id"]: papers.get(node.get("paperId"), {}).get("branch") or "其他"
                       for node in result["nodes"] if node["kind"] == "paper"}
    tree_labels = {topic_id, *branches.values()}

    def height(node):
        measured = heights.get(node["id"], node["height"])
        if node["kind"] == "paper":
            paper = papers.get(node["paperId"], {})
            measured = max(measured, 90 + math.ceil(len(paper.get("title", "")) / 25) * 20
                           + math.ceil(len(node.get("text", "")) / 25) * 20)
        return measured

    def fixed(node):
        return bool(node.get("locked") or node.get("groupId") or node["kind"] == "group"
                    or (node["kind"] == "text" and node["id"] not in tree_labels))

    def rect(node):
        return (node["x"], node["y"], node["width"], height(node))

    obstacles = [rect(node) for node in result["nodes"] if fixed(node)]

    def place(node, x, y):
        if fixed(node):
            return
        width, node_height = node["width"], height(node)
        for _ in range(len(obstacles) + 1):
            overlaps = [r for r in obstacles if x < r[0] + r[2] + 32 and x + width + 32 > r[0]
                        and y < r[1] + r[3] + 36 and y + node_height + 36 > r[1]]
            if not overlaps:
                break
            y = max(r[1] + r[3] for r in overlaps) + 70
        node.update(x=x, y=y)
        obstacles.append(rect(node))

    branch_x, cursor = {}, 80
    for branch, key in branches.items():
        members = [node for node in result["nodes"] if branch_by_paper.get(node["id"]) == branch]
        width = max([nodes[key]["width"]] + [node["width"] for node in members])
        branch_x[branch] = cursor
        cursor += width + 140
    topic = nodes[topic_id]
    place(topic, max(40, (80 + cursor - 140 - topic["width"]) / 2), 60)
    for branch, key in branches.items():
        place(nodes[key], branch_x[branch], topic["y"] + height(topic) + 100)

    cards = [node for node in result["nodes"] if node["kind"] == "paper"]
    successors, predecessors = defaultdict(set), defaultdict(set)
    for edge in result["edges"]:
        if nodes[edge["source"]]["kind"] == nodes[edge["target"]]["kind"] == "paper":
            successors[edge["source"]].add(edge["target"])
            predecessors[edge["target"]].add(edge["source"])

    def year(node):
        value = str(papers.get(node["paperId"], {}).get("year") or "")
        return int(value) if value.isdigit() else 99999

    # Sources precede their evidence-backed successors. Preserve user cycles;
    # remaining cycle members receive a stable order without inventing edges.
    degrees = {node["id"]: len(predecessors[node["id"]]) for node in cards}
    ready = sorted((node for node in cards if not degrees[node["id"]]), key=lambda n: (year(n), n["id"]))
    ordered, visited = [], set()
    while ready:
        node = ready.pop(0)
        ordered.append(node)
        visited.add(node["id"])
        for key in successors[node["id"]]:
            degrees[key] -= 1
            if not degrees[key]:
                ready.append(nodes[key])
        ready.sort(key=lambda n: (year(n), n["id"]))
    ordered.extend(sorted((node for node in cards if node["id"] not in visited), key=lambda n: (year(n), n["id"])))
    baseline = max((nodes[key]["y"] + height(nodes[key]) for key in branches.values()), default=250) + 120
    year_rows = {value: baseline + index * 300 for index, value in enumerate(sorted({year(n) for n in cards}))}
    placed = {node["id"] for node in cards if fixed(node)}
    for node in ordered:
        branch = branch_by_paper[node["id"]]
        y = year_rows[year(node)]
        for parent in predecessors[node["id"]] & placed:
            y = max(y, nodes[parent]["y"] + height(nodes[parent]) + 120)
        place(node, branch_x.get(branch, 80), y)
        placed.add(node["id"])
    return result
