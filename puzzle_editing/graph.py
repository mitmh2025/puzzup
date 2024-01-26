import base64
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO

import matplotlib
import matplotlib.cm
import matplotlib.pyplot as plt
import numpy as np
from django.conf import settings

from puzzle_editing import status
from puzzle_editing.models import PuzzleComment

matplotlib.use("Agg")

rev_status_map = {}
for st in status.STATUSES:
    rev_status_map[status.get_display(st)] = st


timetypes = {
    "1m": timedelta(days=30),
    "2m": timedelta(days=60),
    "3m": timedelta(days=90),
    "4m": timedelta(days=120),
    "2w": timedelta(weeks=2),
    "3w": timedelta(weeks=3),
    "1w": timedelta(weeks=1),
    "3d": timedelta(days=3),
    "1d": timedelta(days=1),
    "2d": timedelta(days=2),
    "4d": timedelta(days=4),
    "5d": timedelta(days=5),
    "6d": timedelta(days=6),
}


exclude = [
    status.INITIAL_IDEA,
    status.AWAITING_APPROVAL,
    status.NEEDS_DISCUSSION,
    status.IDEA_IN_DEVELOPMENT,
    status.AWAITING_ANSWER,
    status.AWAITING_APPROVAL_FOR_TESTSOLVING,
    status.TESTSOLVE_FACTCHECK_REVISION,
    # status.NEEDS_HINTS,
    # status.AWAITING_HINTS_APPROVAL,
    # status.NEEDS_COPY_EDITS,
    # status.NEEDS_FACTCHECK,
    # status.NEEDS_FINAL_REVISIONS,
    status.DEFERRED,
    status.DEAD,
]


def curr_puzzle_graph_b64(time: str, target_count, width: int = 20, height: int = 10):
    comments = (
        PuzzleComment.objects.filter(is_system=True)
        .select_related("puzzle")
        .order_by("date")
    )
    counts: Counter[str] = Counter()
    curr_status: dict[int, str] = {}
    x: list[datetime] = []
    y = []

    labels = [
        status.get_display(s) for s in status.STATUSES[-1::-1] if s not in exclude
    ]

    for comment in comments:
        # print(comment.status_change)
        new_status = comment.status_change
        if new_status:
            counts[new_status] += 1
            if comment.puzzle.id in curr_status:
                counts[curr_status[comment.puzzle.id]] -= 1
            curr_status[comment.puzzle.id] = new_status
            x.append(comment.date)
            y.append([counts[s] for s in status.STATUSES[-1::-1] if s not in exclude])

    # Plot
    fig = plt.figure(figsize=(width, height))
    ax = plt.subplot(1, 1, 1)
    ax.xaxis_date("US/Eastern")
    if time in timetypes:
        now = datetime.now()
        plt.xlim(x[-1] - timetypes[time], now)
    colormap = list(matplotlib.cm.get_cmap("tab20").colors)
    col = (colormap[::2] + colormap[1::2])[: len(status.STATUSES) - len(exclude)]
    ax.stackplot(x, np.transpose(y), labels=labels, colors=col[-1::-1])
    if target_count is not None:
        ax.plot(x, [target_count for i in x], color=(0, 0, 0))
    handles, plabels = ax.get_legend_handles_labels()
    box = ax.get_position()
    ax.set_position([box.x0, box.y0, box.width * 0.8, box.height])
    ax.legend(handles[::-1], plabels[::-1], loc="center left", bbox_to_anchor=(1, 0.5))
    buf = BytesIO()
    fig.savefig(buf, format="png")
    image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8").replace("\n", "")
    buf.close()
    return image_base64


def aggregated_feeder_graph_b64(
    time: str, target_count, width: int = 20, height: int = 10
):
    comments = (
        PuzzleComment.objects.filter(is_system=True)
        .exclude(status_change="")
        .select_related("puzzle")
        .order_by("date")
    )
    counts: Counter[str] = Counter()
    curr_status: dict[int, str] = {}
    x = []
    y = []

    category_to_status = {
        "Writing": [
            status.IDEA_IN_DEVELOPMENT,
            status.AWAITING_ANSWER,
            status.WRITING,
            status.AWAITING_APPROVAL_FOR_TESTSOLVING,
            status.NEEDS_TESTSOLVE_FACTCHECK,
            status.TESTSOLVE_FACTCHECK_REVISION,
            status.REVISING,
        ],
        "Testsolving": [
            status.TESTSOLVING,
            status.ACTIVELY_TESTSOLVING,
            status.AWAITING_TESTSOLVE_REVIEW,
        ],
        "Past testsolving": [
            status.AWAITING_APPROVAL_POST_TESTSOLVING,
            status.NEEDS_HINTS,
            status.AWAITING_HINTS_APPROVAL,
            status.NEEDS_POSTPROD,
            status.POSTPROD_BLOCKED,
            status.ACTIVELY_POSTPRODDING,
            status.POSTPROD_BLOCKED_ON_TECH,
        ],
        "Past postprodding": [
            status.AWAITING_POSTPROD_APPROVAL,
            status.NEEDS_FACTCHECK,
            status.NEEDS_FINAL_REVISIONS,
            status.NEEDS_COPY_EDITS,
            status.DONE,
        ],
    }

    status_to_category = {}
    for cat, statuses in category_to_status.items():
        status_to_category.update({s: cat for s in statuses})

    labels = list(category_to_status.keys())[::-1]
    for comment in comments:
        new_status = comment.status_change
        if new_status not in status_to_category or comment.puzzle.is_meta:
            continue
        counts[status_to_category[new_status]] += 1
        if comment.puzzle.id in curr_status:
            counts[status_to_category[curr_status[comment.puzzle.id]]] -= 1
        curr_status[comment.puzzle.id] = new_status
        x.append(comment.date)
        y.append([counts[cat] for cat in category_to_status][::-1])

    # Plot
    fig = plt.figure(figsize=(width, height))
    ax = plt.subplot(1, 1, 1)
    ax.xaxis_date("US/Eastern")
    if time in timetypes:
        now = datetime.now()
        plt.xlim(x[-1] - timetypes[time], now)
    elif time == "projected":
        end_date = settings.HUNT_TIME
        plt.xlim(x[0], end_date - timedelta(weeks=4))
    elif time == "alltime":
        plt.xlim(x[0], x[-1])
    colormap = list(matplotlib.cm.get_cmap("tab20").colors)
    col = (colormap[::2] + colormap[1::2])[: len(category_to_status)]
    ax.stackplot(x, np.transpose(y), labels=labels, colors=col[-1::-1])
    if target_count is not None:
        ax.plot(
            [*x, settings.HUNT_TIME], [target_count] * (len(x) + 1), color=(0, 0, 0)
        )
        plt.ylim(0, target_count * 1.1)
    handles, plabels = ax.get_legend_handles_labels()
    box = ax.get_position()
    ax.set_position([box.x0, box.y0, box.width * 0.8, box.height])
    ax.legend(handles[::-1], plabels[::-1], loc="center left", bbox_to_anchor=(1, 0.5))
    buf = BytesIO()
    fig.savefig(buf, format="png")
    image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8").replace("\n", "")
    buf.close()
    return image_base64
