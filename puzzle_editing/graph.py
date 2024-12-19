import base64
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO
from typing import Any

import matplotlib
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


exclude = {
    status.INITIAL_IDEA,
    status.AWAITING_ANSWER,
    status.IN_DEVELOPMENT,
    status.DEFERRED,
    status.DEAD,
}


include = list(reversed([s for s in status.STATUSES if s not in exclude]))


def curr_puzzle_graph_b64(time: str, target_count, width: int = 20, height: int = 10):
    comments = (
        PuzzleComment.objects.exclude(status_change="")
        .order_by("date")
        .select_related("puzzle")
    )
    counts = Counter[str]()
    curr_status: dict[int, str] = {}
    x: list[datetime] = []
    y = []

    labels = [status.get_display(s) for s in include]

    for comment in comments:
        new_status = comment.status_change
        if new_status:
            counts[new_status] += 1
            if comment.puzzle.id in curr_status:
                counts[curr_status[comment.puzzle.id]] -= 1
            curr_status[comment.puzzle.id] = new_status
            x.append(comment.date)
            y.append([counts[s] for s in include])

    # Plot
    fig = plt.figure(figsize=(width, height))
    ax = plt.subplot(1, 1, 1)
    ax.xaxis_date(settings.TIME_ZONE)
    if time in timetypes:
        now = datetime.now()
        plt.xlim(x[-1] - timetypes[time], now)
    colormap: list[str] = list(matplotlib.cm.tab20.colors)  # type: ignore
    col = (colormap[::2] + colormap[1::2])[: len(status.STATUSES) - len(exclude)]
    ax.stackplot(np.array(x), np.transpose(y), labels=labels, colors=col[-1::-1])
    if target_count is not None:
        if time not in timetypes:
            testing_glide_path_start = sum(
                c for i, c in enumerate(y[-1]) if status.past_testsolving(include[i])
            )
            factchecking_glide_path_start = sum(
                c for i, c in enumerate(y[-1]) if status.past_factchecking(include[i])
            )
            plt.xlim(right=settings.HUNT_TIME)
            ax.plot(
                np.array([x[-1], settings.HUNT_TIME]),
                np.array([testing_glide_path_start, target_count]),
                "r--",
            )
            ax.plot(
                np.array([x[-1], settings.HUNT_TIME]),
                np.array([factchecking_glide_path_start, target_count]),
                color="orange",
                linestyle="dashed",
            )
        ax.plot(
            np.array(plt.xlim()),
            np.array([target_count, target_count]),
            color=(0, 0, 0),
        )
    handles, plabels = ax.get_legend_handles_labels()
    box = ax.get_position()
    ax.set_position((box.x0, box.y0, box.width * 0.8, box.height))
    ax.legend(handles[::-1], plabels[::-1], loc="center left", bbox_to_anchor=(1, 0.5))
    buf = BytesIO()
    fig.savefig(buf, format="png")
    image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8").replace("\n", "")
    buf.close()
    return image_base64


STATUS_LABELS = {
    "unassigned": "Unassigned",
    "writing": "In writing or revision",
    "testing": "In testing",
    "past_testing": "Past testing",
}


STATUS_COLORS = {
    "unassigned": "#a61c00",
    "writing": "#df4032",
    "testing": "#fbbc02",
    "past_testing": "#33a853",
}


def curr_round_graph_b64(
    byround: list[dict[str, Any]], width: int = 20, height: int = 5
):
    x_max = max(
        r["unassigned"] + r["writing"] + r["testing"] + r["past_testing"]
        for r in byround
    )
    fig, ax = plt.subplots(figsize=(width, height))
    ax.invert_yaxis()
    ax.set_xlim(0, x_max)
    ax.set_ylabel("Round")
    ax.set_xlabel("Number of puzzles (with metas)")

    labels = [r["name"] for r in byround]
    offsets = np.zeros(len(byround))
    for group in ["unassigned", "writing", "testing", "past_testing"]:
        color = STATUS_COLORS[group]
        values = [r[group] for r in byround]
        rects = ax.barh(
            labels,
            values,
            left=offsets,
            color=color,
            height=0.5,
            label=STATUS_LABELS[group],
        )
        r, g, b, *_ = matplotlib.colors.hex2color(color)
        text_color = "white" if r * g * b < 0.5 else "darkgrey"
        ax.bar_label(rects, label_type="center", color=text_color)
        offsets += values

    ax.legend(
        ncols=len(STATUS_LABELS),
        bbox_to_anchor=(0, 1),
        loc="lower left",
        fontsize="small",
    )
    buf = BytesIO()
    fig.savefig(buf, format="png")
    image_base64 = base64.b64encode(buf.getvalue()).decode("utf-8").replace("\n", "")
    buf.close()
    return image_base64
