import datetime

from django import template
from django.conf import settings

register = template.Library()


def display_timedelta(delta):
    """convert a timedelta to a human-readable format"""

    days = ""
    if delta.days:
        days = "1 day, " if delta.days == 1 else f"{delta.days} days, "
    return (
        days
        + f"{delta.seconds // 3600}:{delta.seconds % 3600 // 60:02}:{delta.seconds % 60:02}"
    )


@register.inclusion_tag("tags/countdown.html")
def countdown():
    delta = settings.HUNT_TIME - datetime.datetime.now(datetime.UTC)
    is_down = delta >= datetime.timedelta(0)
    return {
        "countdown": is_down,
        "delta": display_timedelta(abs(delta)),
    }
