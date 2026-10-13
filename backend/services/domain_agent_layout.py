"""Deterministic layout around user locks, groups and independent text."""

import math
from collections import defaultdict
from copy import deepcopy


def arrange(board, papers, heights):
    result = deepcopy(board)
    nodes = {n["id"]: n for n in result["nodes"]}
    cards = [n for n in result["nodes"] if n["kind"] == "paper"]
    year = lambda n: str(papers.get(n["paperId"], {}).get("year") or "9999")
    successors, indegree = defaultdict(set), defaultdict(int)
    for edge in result["edges"]:
        if edge["target"] not in successors[edge["source"]]:
            successors[edge["source"]].add(edge["target"])
            indegree[edge["target"]] += 1
    rank = defaultdict(int)
    ready = sorted([n for n in cards if not indegree[n["id"]]], key=year)
    while ready:
        node = ready.pop(0)
        for target in sorted(successors[node["id"]]):
            rank[target] = max(rank[target], rank[node["id"]] + 1)
            indegree[target] -= 1
            if not indegree[target]:
                ready.append(nodes[target])
    # Cycles (including locked user edges) remain intact; put their members
    # in a stable column instead of failing or inventing a temporal ordering.
    for n in cards:
        if indegree[n["id"]]:
            rank[n["id"]] = 0

    def height(n):
        h = heights.get(n["id"], n["height"])
        if n["kind"] == "paper":
            title = papers.get(n["paperId"], {}).get("title", "")
            h = max(
                h,
                80
                + math.ceil(len(title) / 25) * 20
                + math.ceil(len(n["text"]) / 25) * 20,
            )
        return h

    def rect(n):
        return (n["x"], n["y"], n["width"], height(n))

    def place(n, x, y, obstacles):
        w, h = n["width"], height(n)
        for _ in range(len(obstacles) + 1):
            hit = next(
                (
                    r
                    for r in obstacles
                    if x < r[0] + r[2] + 32
                    and x + w + 32 > r[0]
                    and y < r[1] + r[3] + 36
                    and y + h + 36 > r[1]
                ),
                None,
            )
            if hit is None:
                break
            y = hit[1] + hit[3] + 60
        n.update(x=x, y=y)
        obstacles.append(rect(n))

    groups = [n for n in result["nodes"] if n["kind"] == "group"]
    fixed = [
        rect(n)
        for n in result["nodes"]
        if not n.get("groupId")
        and (n["kind"] == "text" or (n["kind"] == "paper" and n["locked"]))
    ]
    movable_groups = []
    for group in groups:
        members = [n for n in result["nodes"] if n.get("groupId") == group["id"]]
        if group["locked"]:
            fixed.append(rect(group))
            continue
        original_members = deepcopy(members)
        obstacles = [rect(n) for n in members if n["kind"] != "paper" or n["locked"]]
        free = [n for n in members if n["kind"] == "paper" and not n["locked"]]
        min_rank = min((rank[n["id"]] for n in free), default=0)
        for n in sorted(free, key=lambda n: (rank[n["id"]], year(n), n["id"])):
            place(
                n,
                group["x"] + 24 + min(20, rank[n["id"]] - min_rank) * 360,
                group["y"] + 60,
                obstacles,
            )
        if members:
            width = max(
                group["width"],
                max(n["x"] + n["width"] for n in members) - group["x"] + 30,
            )
            group_height = max(
                group["height"],
                max(n["y"] + height(n) for n in members) - group["y"] + 30,
            )
            if width > 10000 or group_height > 10000:
                # Keep the user's arrangement if its contents cannot fit in a
                # legal group; never clip the newly arranged cards outside it.
                for n, original in zip(members, original_members):
                    n.update(original)
            else:
                group.update(width=width, height=group_height)
                heights = dict(heights, **{group["id"]: group["height"]})
        if any(n["locked"] for n in members):
            fixed.append(rect(group))
        else:
            movable_groups.append((group, members))
    for group, members in movable_groups:
        old_x, old_y = group["x"], group["y"]
        place(group, 80, 80, fixed)
        for n in members:
            n["x"] += group["x"] - old_x
            n["y"] += group["y"] - old_y
    for n in sorted(cards, key=lambda n: (rank[n["id"]], year(n), n["id"])):
        if not n.get("groupId") and not n["locked"]:
            place(n, 80 + rank[n["id"]] * 360, 80, fixed)
    return result
