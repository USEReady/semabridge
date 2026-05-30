"""Email sending service for auth flows (password reset, etc.)"""
import logging
import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

logger = logging.getLogger(__name__)


def _get_smtp_config() -> dict:
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT", "587")),
        "user": os.environ.get("SMTP_USER", ""),
        "password": os.environ.get("SMTP_PASSWORD", ""),
        "from_address": os.environ.get(
            "SMTP_FROM_ADDRESS",
            os.environ.get("SMTP_USER", "noreply@semabridge.com"),
        ),
        "use_tls": os.environ.get("SMTP_USE_TLS", "true").lower() == "true",
    }


def send_password_reset_email(to_email: str, reset_token: str, username: str) -> bool:
    """Send a password reset email.

    Returns True on success, False on failure.  Never raises — email
    failures must not break the API response.
    """
    config = _get_smtp_config()

    if not config["host"] or not config["user"]:
        logger.warning(
            "[EmailService] SMTP not configured (SMTP_HOST/SMTP_USER missing). "
            "Password reset email NOT sent to %s. Token prefix: %s",
            to_email,
            reset_token[:8] + "...",
        )
        # In dev mode, print the full reset token directly to stdout so it
        # is never truncated by Rich's line-wrapping logger.
        if os.environ.get("AUTH_ENABLED", "true").lower() == "false":
            frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
            reset_url = f"{frontend_url}/auth/reset-password/{reset_token}"
            print(
                f"\n{'='*60}\n"
                f"[DEV] PASSWORD RESET TOKEN for {to_email}\n"
                f"Token : {reset_token}\n"
                f"URL   : {reset_url}\n"
                f"{'='*60}\n",
                flush=True,
            )
        return False

    frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
    reset_url = f"{frontend_url}/auth/reset-password/{reset_token}"
    expiry_minutes = int(os.environ.get("RESET_TOKEN_EXPIRY_MINUTES", "60"))

    subject = "SemaBridge — Reset Your Password"
    body_html = f"""
    <html><body>
    <p>Hi {username},</p>
    <p>You requested a password reset for your SemaBridge account.</p>
    <p>
      <a href="{reset_url}"
         style="background:#2563eb;color:white;padding:12px 24px;
                text-decoration:none;border-radius:6px;display:inline-block;">
        Reset Password
      </a>
    </p>
    <p>Or copy this link: {reset_url}</p>
    <p>
      This link expires in {expiry_minutes} minutes.
      If you did not request this, you can ignore this email.
    </p>
    <p>— The SemaBridge Team</p>
    </body></html>
    """
    body_text = (
        f"Hi {username},\n\n"
        f"Reset your SemaBridge password:\n{reset_url}\n\n"
        f"Expires in {expiry_minutes} minutes. "
        f"Ignore if you did not request this."
    )

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["from_address"]
        msg["To"] = to_email
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(config["host"], config["port"]) as smtp:
            smtp.ehlo()
            if config["use_tls"]:
                smtp.starttls()
                smtp.ehlo()
            if config["user"] and config["password"]:
                smtp.login(config["user"], config["password"])
            smtp.sendmail(config["from_address"], [to_email], msg.as_string())

        logger.info("[EmailService] Password reset email sent to %s", to_email)
        return True
    except Exception as exc:
        logger.error(
            "[EmailService] Failed to send reset email to %s: %s", to_email, exc
        )
        return False
