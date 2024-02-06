# Based off the documentation at https://docs.djangoproject.com/en/4.0/topics/i18n/timezones/#selecting-the-current-time-zone
import zoneinfo
from asyncio import iscoroutinefunction

from django.utils import timezone
from django.utils.decorators import sync_and_async_middleware


@sync_and_async_middleware
def timezone_middleware(get_response):
    def set_timezone(user):
        tzname = user.timezone if user.is_authenticated else None
        if tzname:
            timezone.activate(zoneinfo.ZoneInfo(tzname))
        else:
            timezone.deactivate()

    if iscoroutinefunction(get_response):

        async def middleware(request):
            set_timezone(await request.auser())
            return await get_response(request)
    else:

        def middleware(request):
            set_timezone(request.user)
            return get_response(request)

    return middleware


# Use this middleware to start the Discord background task, because middlewares
# only get loaded in an HTTP server context (not, e.g., under management
# commands)
@sync_and_async_middleware
def discord_daemon_middleware(get_response):
    from twisted.internet import reactor

    from . import discord_daemon

    reactor.callWhenRunning(discord_daemon.twisted_main)

    return get_response
