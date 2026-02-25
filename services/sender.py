# services/sender.py — Gmail SMTP version
import smtplib
import uuid as uuid_lib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr, make_msgid
from config import settings


def get_sender_for_campaign(campaign_id: str) -> dict:
    """With Gmail we only have one sender — always returns the same"""
    return {
        "name": settings.primary_sender_name,
        "email": settings.gmail_address,
    }


def _get_smtp_connection():
    """Create and return an authenticated Gmail SMTP connection"""
    server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
    server.login(settings.gmail_address, settings.gmail_app_password)
    return server


def send_email(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
    sender: dict,
    reply_to: str = None,
    custom_args: dict = None,   # not used in Gmail version, kept for compatibility
) -> dict:
    """Send a single email via Gmail SMTP"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = formataddr((sender["name"], sender["email"]))
    msg["To"]      = formataddr((to_name or "", to_email))
    msg["Reply-To"] = reply_to or settings.reply_to_email

    # Generate a unique Message-ID for threading
    message_id = make_msgid(domain="gmail.com")
    msg["Message-ID"] = message_id

    # Attach HTML content
    msg.attach(MIMEText(html_body, "html"))

    try:
        with _get_smtp_connection() as server:
            server.sendmail(sender["email"], to_email, msg.as_string())

        return {
            "status_code": 200,
            "message_id": message_id,
        }

    except smtplib.SMTPRecipientsRefused:
        raise Exception(f"Email address rejected by Gmail: {to_email}")
    except smtplib.SMTPAuthenticationError:
        raise Exception("Gmail authentication failed — check GMAIL_APP_PASSWORD in .env")
    except Exception as e:
        raise Exception(f"Gmail SMTP error: {str(e)}")


def send_reply_email(
    to_email: str,
    to_name: str,
    body: str,
    sender: dict,
    in_reply_to_message_id: str = None,
) -> dict:
    """Send a reply email maintaining the thread"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "Re: following up"
    msg["From"]    = formataddr((sender["name"], sender["email"]))
    msg["To"]      = formataddr((to_name or "", to_email))

    # Thread headers — tells email clients this is a reply
    if in_reply_to_message_id:
        msg["In-Reply-To"] = in_reply_to_message_id
        msg["References"]  = in_reply_to_message_id

    html = f"<p>{body.replace(chr(10), '<br>')}</p>"
    msg.attach(MIMEText(html, "html"))

    try:
        with _get_smtp_connection() as server:
            server.sendmail(sender["email"], to_email, msg.as_string())
        return {"status_code": 200}
    except Exception as e:
        raise Exception(f"Gmail SMTP reply error: {str(e)}")