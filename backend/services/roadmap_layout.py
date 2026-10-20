"""Deterministic roadmap layouts around protected user content."""

import math
from collections import Counter, defaultdict
from copy import deepcopy


ORIGIN_X = 80
ORIGIN_Y = 60
TOPIC_GAP = 100
BRANCH_CONTENT_GAP = 100
BRANCH_GAP = 140
SIBLING_GAP = 70
SIBLING_ROW_GAP = 44
MAX_SIBLINGS_PER_ROW = 2
MAX_SIBLINGS_PER_COLUMN = 2
RANK_GAP = 100
BLOCK_GAP = 90
GROUP_PADDING = 24
GROUP_HEADER = 62


def arrange_tree(board, papers, heights, topic_id, branches, orientation="horizontal"):
    """Lay out a topic, its branch lanes and each lane's evidence graph.

    Horizontal layout fans branches across a row and develops papers downward.
    Vertical layout stacks branches and develops papers from left to right.
    Locked items, notes, and user groups containing annotations remain exactly
    where the user put them. Empty, unlocked paper-only groups from the legacy
    roadmap layout are safe to migrate and become branch-lane containers.
    """

    if orientation not in {"horizontal", "vertical"}:
        raise ValueError(f"Unsupported roadmap layout orientation: {orientation}")

    result = deepcopy(board)
    nodes = {node["id"]: node for node in result["nodes"]}
    cards = [node for node in result["nodes"] if node["kind"] == "paper"]
    branch_by_card = {
        node["id"]: papers.get(node.get("paperId"), {}).get("branch") or "其他"
        for node in cards
    }
    tree_labels = {topic_id, *branches.values()}
    members_by_group = defaultdict(list)
    for node in result["nodes"]:
        if node.get("groupId"):
            members_by_group[node["groupId"]].append(node)

    def measured_height(node):
        measured = heights.get(node["id"], node["height"])
        if node["kind"] == "paper":
            paper = papers.get(node.get("paperId"), {})
            measured = max(
                measured,
                90
                + math.ceil(len(paper.get("title", "")) / 25) * 20
                + math.ceil(len(node.get("text", "")) / 25) * 20,
            )
        return measured

    def migratable_group(group):
        members = members_by_group.get(group["id"], [])
        return bool(
            not group.get("locked")
            and members
            and all(
                member["kind"] == "paper"
                and not member.get("locked")
                and not member.get("text", "").strip()
                and branch_by_card.get(member["id"])
                for member in members
            )
        )

    movable_groups = {
        node["id"]: node
        for node in result["nodes"]
        if node["kind"] == "group" and migratable_group(node)
    }

    def fixed(node):
        if node.get("locked"):
            return True
        if node["kind"] == "group":
            return node["id"] not in movable_groups
        if node.get("groupId"):
            return node["groupId"] not in movable_groups
        return node["kind"] == "text" and node["id"] not in tree_labels

    def year(node):
        value = str(papers.get(node.get("paperId"), {}).get("year") or "")
        return int(value) if value.isdigit() else 99999

    # Development ranks are computed separately for every research branch.
    # Cross-branch evidence stays visible but cannot distort either lane.
    successors = defaultdict(set)
    predecessors = defaultdict(set)
    for edge in result["edges"]:
        source, target = nodes.get(edge["source"]), nodes.get(edge["target"])
        if not source or not target or source["kind"] != "paper" or target["kind"] != "paper":
            continue
        if branch_by_card.get(source["id"]) != branch_by_card.get(target["id"]):
            continue
        successors[source["id"]].add(target["id"])
        predecessors[target["id"]].add(source["id"])

    ranks = defaultdict(int)
    for branch in branches:
        branch_cards = [card for card in cards if branch_by_card.get(card["id"]) == branch]
        branch_ids = {card["id"] for card in branch_cards}
        indegree = {card["id"]: len(predecessors[card["id"]] & branch_ids) for card in branch_cards}
        ready = sorted(
            (card for card in branch_cards if not indegree[card["id"]]),
            key=lambda node: (year(node), node["id"]),
        )
        visited = set()
        while ready:
            node = ready.pop(0)
            visited.add(node["id"])
            for target in sorted(successors[node["id"]] & branch_ids):
                ranks[target] = max(ranks[target], ranks[node["id"]] + 1)
                indegree[target] -= 1
                if not indegree[target]:
                    ready.append(nodes[target])
            ready.sort(key=lambda item: (year(item), item["id"]))
        # User-protected cycles are retained. Keeping unresolved cycle members
        # in the entry column is stable and does not invent a progression.
        for card in branch_cards:
            if card["id"] not in visited:
                ranks[card["id"]] = 0

    # Build compact visual blocks. A legacy group remains a real editable
    # group, but its blank cards are arranged as a development graph inside it.
    blocks_by_branch = defaultdict(list)
    for group_id, group in movable_groups.items():
        grouped = defaultdict(list)
        for member in members_by_group[group_id]:
            grouped[branch_by_card[member["id"]]].append(member)
        dominant = Counter({name: len(members) for name, members in grouped.items()}).most_common(1)[0][0]
        members_by_group[group_id] = grouped[dominant]
        blocks_by_branch[dominant].append((group, grouped[dominant]))
        # Old generated groups occasionally combined two later-refined
        # branches. Blank cards can be detached safely so every paper appears
        # beneath the branch selected by the current research metadata.
        for name, members in grouped.items():
            if name != dominant:
                for member in members:
                    member["groupId"] = None
    for branch in branches:
        ungrouped = [
            card
            for card in cards
            if branch_by_card.get(card["id"]) == branch and not card.get("groupId") and not fixed(card)
        ]
        if ungrouped:
            blocks_by_branch[branch].append((None, ungrouped))
        blocks_by_branch[branch].sort(
            key=lambda pair: (
                min((year(card) for card in pair[1]), default=99999),
                pair[0].get("text", "") if pair[0] else "",
            )
        )

    def horizontal_block_metrics(group, block_cards):
        buckets = defaultdict(list)
        for card in block_cards:
            buckets[ranks[card["id"]]].append(card)
        for bucket in buckets.values():
            bucket.sort(key=lambda card: (year(card), card["id"]))
        rows = {
            rank: [
                bucket[index:index + MAX_SIBLINGS_PER_ROW]
                for index in range(0, len(bucket), MAX_SIBLINGS_PER_ROW)
            ]
            for rank, bucket in buckets.items()
        }
        content_width = max(
            (
                sum(card["width"] for card in row) + SIBLING_GAP * max(0, len(row) - 1)
                for rank_rows in rows.values()
                for row in rank_rows
            ),
            default=280,
        )
        rank_heights = [
            sum(max((measured_height(card) for card in row), default=0) for row in rows[rank])
            + SIBLING_ROW_GAP * max(0, len(rows[rank]) - 1)
            for rank in sorted(rows)
        ]
        content_height = sum(rank_heights) + RANK_GAP * max(0, len(rank_heights) - 1)
        if group:
            return buckets, max(640, content_width + GROUP_PADDING * 2), max(
                220, content_height + GROUP_HEADER + GROUP_PADDING * 2,
            )
        return buckets, content_width, max(180, content_height)

    def vertical_block_metrics(group, block_cards):
        buckets = defaultdict(list)
        for card in block_cards:
            buckets[ranks[card["id"]]].append(card)
        for bucket in buckets.values():
            bucket.sort(key=lambda card: (year(card), card["id"]))
        columns = {
            rank: [
                bucket[index:index + MAX_SIBLINGS_PER_COLUMN]
                for index in range(0, len(bucket), MAX_SIBLINGS_PER_COLUMN)
            ]
            for rank, bucket in buckets.items()
        }
        rank_widths = [
            sum(
                max((card["width"] for card in column), default=0)
                for column in columns[rank]
            )
            + SIBLING_ROW_GAP * max(0, len(columns[rank]) - 1)
            for rank in sorted(columns)
        ]
        content_width = sum(rank_widths) + RANK_GAP * max(0, len(rank_widths) - 1)
        content_height = max(
            (
                sum(measured_height(card) for card in column)
                + SIBLING_GAP * max(0, len(column) - 1)
                for rank_columns in columns.values()
                for column in rank_columns
            ),
            default=180,
        )
        if group:
            return buckets, max(640, content_width + GROUP_PADDING * 2), max(
                220, content_height + GROUP_HEADER + GROUP_PADDING * 2,
            )
        return buckets, max(280, content_width), content_height

    metrics = vertical_block_metrics if orientation == "vertical" else horizontal_block_metrics
    lane_data = []
    for branch, branch_id in branches.items():
        block_data = []
        for group, block_cards in blocks_by_branch[branch]:
            buckets, block_width, block_height = metrics(group, block_cards)
            block_data.append((group, buckets, block_width, block_height))
        branch_node = nodes[branch_id]
        if orientation == "vertical":
            content_width = (
                sum(item[2] for item in block_data)
                + BLOCK_GAP * max(0, len(block_data) - 1)
            )
            lane_width = branch_node["width"] + BRANCH_CONTENT_GAP + max(280, content_width)
            lane_height = max(
                [measured_height(branch_node) + 80, *(item[3] for item in block_data)],
                default=220,
            )
        else:
            lane_width = max([branch_node["width"] + 80, *(item[2] for item in block_data)], default=360)
            content_height = sum(item[3] for item in block_data) + BLOCK_GAP * max(0, len(block_data) - 1)
            lane_height = measured_height(branch_node) + BRANCH_CONTENT_GAP + max(180, content_height)
        lane_data.append((branch, branch_id, block_data, lane_width, lane_height))

    # Keep the generated tree clear of protected user material. If the topic
    # itself is locked it remains the anchor and the rest grows from it.
    topic = nodes[topic_id]
    if orientation == "vertical":
        planned_width = topic["width"] + TOPIC_GAP + max((item[3] for item in lane_data), default=360)
        planned_height = (
            sum(item[4] for item in lane_data)
            + BRANCH_GAP * max(0, len(lane_data) - 1)
        )
    else:
        planned_width = sum(item[3] for item in lane_data) + BRANCH_GAP * max(0, len(lane_data) - 1)
        planned_height = measured_height(topic) + TOPIC_GAP + max((item[4] for item in lane_data), default=220)
    origin_x, origin_y = ORIGIN_X, ORIGIN_Y
    if topic.get("locked"):
        if orientation == "vertical":
            origin_x = topic["x"]
            origin_y = topic["y"] + measured_height(topic) / 2 - planned_height / 2
        else:
            origin_x = topic["x"] + topic["width"] / 2 - planned_width / 2
            origin_y = topic["y"]
    fixed_rects = [
        (node["x"], node["y"], node["width"], measured_height(node))
        for node in result["nodes"]
        if fixed(node)
    ]
    if not topic.get("locked"):
        collisions = [
            rect
            for rect in fixed_rects
            if origin_x < rect[0] + rect[2] + 80
            and origin_x + planned_width + 80 > rect[0]
            and origin_y < rect[1] + rect[3] + 80
            and origin_y + planned_height + 80 > rect[1]
        ]
        if collisions:
            if orientation == "vertical":
                origin_x = max(rect[0] + rect[2] for rect in collisions) + 180
            else:
                origin_y = max(rect[1] + rect[3] for rect in collisions) + 180

    if orientation == "vertical":
        branch_x = origin_x + topic["width"] + TOPIC_GAP
        cursor_y = origin_y
        for _branch, branch_id, block_data, _lane_width, lane_height in lane_data:
            branch_node = nodes[branch_id]
            if not fixed(branch_node):
                branch_node.update(
                    x=branch_x,
                    y=cursor_y + (lane_height - measured_height(branch_node)) / 2,
                )
            block_x = branch_x + branch_node["width"] + BRANCH_CONTENT_GAP
            for group, buckets, block_width, block_height in block_data:
                block_y = cursor_y + (lane_height - block_height) / 2
                content_left = block_x + (GROUP_PADDING if group else 0)
                content_top = block_y + (GROUP_HEADER + GROUP_PADDING if group else 0)
                card_x = content_left
                for _rank, bucket in sorted(buckets.items()):
                    columns = [
                        bucket[index:index + MAX_SIBLINGS_PER_COLUMN]
                        for index in range(0, len(bucket), MAX_SIBLINGS_PER_COLUMN)
                    ]
                    for column in columns:
                        column_width = max((card["width"] for card in column), default=0)
                        column_height = (
                            sum(measured_height(card) for card in column)
                            + SIBLING_GAP * max(0, len(column) - 1)
                        )
                        reserved_header = GROUP_HEADER + GROUP_PADDING * 2 if group else 0
                        card_y = content_top + (
                            block_height - reserved_header - column_height
                        ) / 2
                        for card in column:
                            card.update(x=card_x, y=card_y)
                            card_y += measured_height(card) + SIBLING_GAP
                        card_x += column_width + SIBLING_ROW_GAP
                    card_x += RANK_GAP - (SIBLING_ROW_GAP if columns else 0)
                if group:
                    group.update(x=block_x, y=block_y, width=block_width, height=block_height)
                block_x += block_width + BLOCK_GAP
            cursor_y += lane_height + BRANCH_GAP

        if not fixed(topic):
            topic.update(
                x=origin_x,
                y=origin_y + (planned_height - measured_height(topic)) / 2,
            )
        return result

    branch_y = origin_y + measured_height(topic) + TOPIC_GAP
    cursor_x = origin_x
    for _branch, branch_id, block_data, lane_width, _lane_height in lane_data:
        branch_node = nodes[branch_id]
        if not fixed(branch_node):
            branch_node.update(x=cursor_x + (lane_width - branch_node["width"]) / 2, y=branch_y)
        block_y = branch_y + measured_height(branch_node) + BRANCH_CONTENT_GAP
        for group, buckets, block_width, block_height in block_data:
            content_top = block_y + (GROUP_HEADER + GROUP_PADDING if group else 0)
            card_y = content_top
            for _rank, bucket in sorted(buckets.items()):
                rows = [
                    bucket[index:index + MAX_SIBLINGS_PER_ROW]
                    for index in range(0, len(bucket), MAX_SIBLINGS_PER_ROW)
                ]
                for row in rows:
                    row_width = sum(card["width"] for card in row) + SIBLING_GAP * max(0, len(row) - 1)
                    card_x = cursor_x + (lane_width - row_width) / 2
                    row_height = max((measured_height(card) for card in row), default=0)
                    for card in row:
                        card.update(x=card_x, y=card_y)
                        card_x += card["width"] + SIBLING_GAP
                    card_y += row_height + SIBLING_ROW_GAP
                card_y += RANK_GAP - SIBLING_ROW_GAP
            if group:
                group.update(
                    x=cursor_x + (lane_width - block_width) / 2,
                    y=block_y,
                    width=block_width,
                    height=block_height,
                )
            block_y += block_height + BLOCK_GAP
        cursor_x += lane_width + BRANCH_GAP

    if not fixed(topic):
        topic.update(x=origin_x + (planned_width - topic["width"]) / 2, y=origin_y)
    return result
