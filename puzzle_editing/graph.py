import base64
from collections import Counter
from datetime import datetime, timedelta
from io import BytesIO

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
        PuzzleComment.objects.filter(is_system=True)
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
            glide_path_start = sum(
                c for i, c in enumerate(y[-1]) if status.past_testsolving(include[i])
            )
            plt.xlim(right=settings.HUNT_TIME)
            ax.plot(
                np.array([x[-1], settings.HUNT_TIME]),
                np.array([glide_path_start, target_count]),
                "r--",
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
