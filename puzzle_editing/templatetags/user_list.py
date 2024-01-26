from django import template

from puzzle_editing.models import User

register = template.Library()


@register.simple_tag
def user_list(users, linkify=False):
    """Displays a QuerySet of users"""

    try:
        iter(users)
    except TypeError:
        users = users.all()
    return User.html_user_list_of(users, linkify)
