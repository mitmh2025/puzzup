from django import template

register = template.Library()


@register.filter()
def name_list(users):
    """Displays a comma-delimited list of users"""
    return ", ".join([user.credits_name for user in users])
