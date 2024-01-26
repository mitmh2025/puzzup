from django import template

register = template.Library()


@register.filter()
def check_permission(user, permission):
    return user.user_permissions.filter(codename=permission).exists()


@register.filter(name="has_group")
def has_group(user, group_name):
    return user.is_superuser or user.groups.filter(name=group_name).exists()
