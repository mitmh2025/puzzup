from django.conf import settings

def auto_postprodding_enabled(_request):
    return {
        "AUTO_POSTPRODDING_ENABLED": settings.HUNT_REPO_URL != ""
    }
