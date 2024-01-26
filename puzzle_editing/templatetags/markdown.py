from bleach import Cleaner, sanitizer
from bleach.linkifier import LinkifyFilter
from django import template
from django.template.loader import render_to_string
from django.utils.safestring import mark_safe
from markdown import markdown as convert_markdown
from pymdownx import emoji

register = template.Library()

SAFE_TAGS = [
    "a",
    "abbr",
    "acronym",
    "b",
    "big",
    "blockquote",
    "br",
    "cite",
    "code",
    "dd",
    "del",
    "div",
    "dl",
    "dt",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "hr",
    "i",
    "img",
    "ins",
    "li",
    "ol",
    "p",
    "pre",
    "q",
    "s",
    "small",
    "span",
    "sub",
    "sup",
    "strike",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tfoot",
    "tr",
    "u",
    "ul",
]


def allow_img_attrs(tag, name, value):
    return name in ("alt", "class", "src", "height", "width")


# LinkifyFilter converts raw URLs in text into links
cleaner = Cleaner(
    tags=SAFE_TAGS,
    filters=[LinkifyFilter],
    attributes=sanitizer.ALLOWED_ATTRIBUTES | {"img": allow_img_attrs},
)


@register.simple_tag(takes_context=True)
def include_markdown(context, template_name):
    template = render_to_string(template_name, {})
    return mark_safe(convert_markdown(template, extensions=["extra", "nl2br"]))


@register.filter
def markdown(text):
    if text is None:
        return text
    return mark_safe(
        cleaner.clean(
            convert_markdown(
                text,
                extensions=["extra", "nl2br", "pymdownx.emoji"],
                extension_configs={
                    "pymdownx.emoji": {
                        "emoji_index": emoji.gemoji,
                        "emoji_generator": emoji.to_alt,
                        "alt": "unicode",
                    }
                },
            )
        )
    )
