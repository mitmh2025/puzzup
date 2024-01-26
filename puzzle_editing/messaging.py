from django.conf import settings
from django.core.mail.message import EmailMultiAlternatives
from django.template.loader import render_to_string


def send_mail_wrapper(subject, template, context, recipients):
    if settings.EMAIL_ENABLED and recipients:
        mail = EmailMultiAlternatives(
            subject=settings.EMAIL_SUBJECT_PREFIX + subject,
            body=render_to_string(template + ".txt", context),
            from_email=f"TTBNL Puzzup no-reply <{settings.DEFAULT_FROM_EMAIL}>",
            to=recipients,
            alternatives=[(render_to_string(template + ".html", context), "text/html")],
            reply_to=[f"TTBNL Puzzup no-reply <{settings.DEFAULT_FROM_EMAIL}>"],
        )
        send_res = mail.send()
        if send_res != 1:
            msg = f"Unknown failure sending mail??? {recipients} {send_res}"
            raise RuntimeError(msg)
