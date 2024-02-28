# Based off the documentation at https://docs.djangoproject.com/en/4.0/topics/i18n/timezones/#selecting-the-current-time-zone
import zoneinfo

from django.utils import timezone


def timezone_middleware(get_response):
    def middleware(request):
        tzname = request.user.timezone if request.user.is_authenticated else None
        if tzname:
            timezone.activate(zoneinfo.ZoneInfo(tzname))
        else:
            timezone.deactivate()
        return get_response(request)

    return middleware


# Use this middleware to start the Discord background task, because middlewares
# only get loaded in an HTTP server context (not, e.g., under management
# commands)
def discord_daemon_middleware(get_response):
    from twisted.internet import reactor

    from . import discord_daemon

    reactor.callWhenRunning(discord_daemon.twisted_main)

    return get_response
