"""
Context processor to make the testsolving_enabled variable available to templates.
"""
from django.http import HttpRequest
from puzzle_editing import models


def site_settings(_request: HttpRequest) -> bool:
    """Returns true if testsolving is enabled."""
    return {
        "TESTSOLVING_ENABLED": not models.SiteSetting.get_bool_setting("TESTSOLVING_DISABLED")
    }
