from django import template, urls

register = template.Library()


@register.inclusion_tag("tags/nav_link.html")
def nav_link(current_path, url_name, text):
    url = urls.reverse(url_name)

    selected = current_path == url if url == "/" else current_path.startswith(url)

    return {
        "url": url,
        "selected": selected,
        "text": text,
    }
