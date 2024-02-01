from django import template

register = template.Library()


@register.inclusion_tag("tags/logistics_info.html")
def logistics_info(logistics_info, difficulty_form, perms):
    return {
        "logistics_info": logistics_info,
        "difficulty_form": difficulty_form,
        "perms": perms,
    }
