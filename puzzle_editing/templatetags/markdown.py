from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from markdown import markdown as convert_markdown
from nh3 import clean
from pymdownx import emoji  # type: ignore

register = template.Library()


@register.simple_tag(takes_context=False)
def include_markdown(template_name):
    template = render_to_string(template_name, {})
    return mark_safe(convert_markdown(template, extensions=["extra", "nl2br"]))


@register.filter
def markdown(text):
    if text is None:
        return text
    return mark_safe(
        clean(
            convert_markdown(
                text,
                extensions=["extra", "nl2br", "pymdownx.emoji", "pymdownx.magiclink"],
                extension_configs={
                    "pymdownx.emoji": {
                        "emoji_index": emoji.gemoji,
                        "emoji_generator": emoji.to_alt,
                        "alt": "unicode",
                    },
                },
            )
        )
    )
