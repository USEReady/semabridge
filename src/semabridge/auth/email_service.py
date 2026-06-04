"""Email sending service for auth flows and run notifications."""
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


def send_password_reset_email(to_email: str, reset_token: str, username: str) -> "tuple[bool, str | None]":
    """Send a password reset email.

    Returns True on success, False on failure.  Never raises — email
    failures must not break the API response.
    """
    config = _get_smtp_config()

    if not config["host"] or not config["user"]:
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")
        reset_url = f"{frontend_url}/auth/reset-password/{reset_token}"
        # Always print full reset URL when SMTP is not configured so the
        # developer can complete the flow without reading truncated logs.
        print(
            f"\n{'='*60}\n"
            f"[DEV] PASSWORD RESET — SMTP not configured\n"
            f"User  : {to_email}\n"
            f"Token : {reset_token}\n"
            f"URL   : {reset_url}\n"
            f"{'='*60}\n",
            flush=True,
        )
        logger.warning(
            "[EmailService] SMTP not configured — reset URL printed to stdout for %s",
            to_email,
        )
        return False, reset_url

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
        return True, None
    except Exception as exc:
        logger.error(
            "[EmailService] Failed to send reset email to %s: %s", to_email, exc
        )
        return False, None


def send_run_summary_notification(
    project_name: str,
    project_id: str,
    run_id: str,
    status: str,
    changes_summary: dict,
    results: list,
    recipient_email: str,
    frontend_url: str = "",
) -> bool:
    """Send a post-run summary email when a sync completes with warnings or failures.

    Returns True on success, False on failure.  Never raises.
    """
    config = _get_smtp_config()
    if not frontend_url:
        frontend_url = os.environ.get("FRONTEND_URL", "http://localhost:5173")

    run_url = f"{frontend_url}/jobs"

    status_label = {
        "success": "Completed Successfully",
        "warning": "Completed with Warnings",
        "partial": "Completed Partially",
        "failed": "Failed",
    }.get(str(status).lower(), str(status).title())

    status_color = {
        "success": "#16a34a",
        "warning": "#d97706",
        "partial": "#d97706",
        "failed": "#dc2626",
    }.get(str(status).lower(), "#6b7280")

    # Build change summary text
    summary_lines = []
    if isinstance(changes_summary, dict):
        for key, val in changes_summary.items():
            if val and int(val) > 0:
                label = key.replace("_", " ").title()
                summary_lines.append(f"  • {label}: {val}")

    # Build conflict list (first 10 only)
    conflict_lines = []
    if isinstance(results, list):
        errors = [r for r in results if isinstance(r, dict) and r.get("status") in ("error", "failed", "warning")]
        for r in errors[:10]:
            model = r.get("model_name") or r.get("model") or ""
            msg = r.get("message") or r.get("error") or ""
            conflict_lines.append(f"  • {model}: {msg}" if model else f"  • {msg}")

    summary_block = "\n".join(summary_lines) if summary_lines else "  (no change details available)"
    conflict_block = "\n".join(conflict_lines) if conflict_lines else ""

    subject = f"[SemaBridge] Sync {status_label} — {project_name}"

    body_text = (
        f"SemaBridge Sync Notification\n"
        f"{'='*40}\n"
        f"Project : {project_name}\n"
        f"Status  : {status_label}\n"
        f"Run ID  : {run_id}\n\n"
        f"Changes Summary:\n{summary_block}\n"
        + (f"\nIssues Detected:\n{conflict_block}\n" if conflict_block else "")
        + f"\nView run details: {run_url}\n"
    )

    body_html = f"""
    <html><body style="font-family:sans-serif;color:#1f2937;max-width:600px;margin:auto;">
    <h2 style="color:{status_color}">SemaBridge — Sync {status_label}</h2>
    <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
      <tr><td style="padding:4px 8px;font-weight:600;width:100px;">Project</td>
          <td style="padding:4px 8px;">{project_name}</td></tr>
      <tr style="background:#f9fafb;"><td style="padding:4px 8px;font-weight:600;">Status</td>
          <td style="padding:4px 8px;color:{status_color};font-weight:600;">{status_label}</td></tr>
      <tr><td style="padding:4px 8px;font-weight:600;">Run ID</td>
          <td style="padding:4px 8px;font-size:12px;color:#6b7280;">{run_id}</td></tr>
    </table>
    {"<h3>Changes Summary</h3><ul>" + "".join(f"<li>{l.strip(' •')}</li>" for l in summary_lines) + "</ul>" if summary_lines else ""}
    {"<h3 style='color:#dc2626'>Issues Detected</h3><ul style='color:#dc2626'>" + "".join(f"<li>{l.strip(' •')}</li>" for l in conflict_lines) + "</ul>" if conflict_lines else ""}
    <p style="margin-top:24px;">
      <a href="{run_url}"
         style="background:#2563eb;color:white;padding:10px 20px;
                text-decoration:none;border-radius:6px;display:inline-block;">
        View Run Details
      </a>
    </p>
    <p style="font-size:11px;color:#9ca3af;margin-top:16px;">— The SemaBridge Team</p>
    </body></html>
    """

    if not config["host"] or not config["user"]:
        print(
            f"\n{'='*60}\n"
            f"[DEV] RUN NOTIFICATION — SMTP not configured\n"
            f"To     : {recipient_email}\n"
            f"Subject: {subject}\n"
            f"{body_text}"
            f"{'='*60}\n",
            flush=True,
        )
        logger.warning("[EmailService] SMTP not configured — run notification printed to stdout for %s", recipient_email)
        return False

    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"] = config["from_address"]
        msg["To"] = recipient_email
        msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

        with smtplib.SMTP(config["host"], config["port"]) as smtp:
            smtp.ehlo()
            if config["use_tls"]:
                smtp.starttls()
                smtp.ehlo()
            if config["user"] and config["password"]:
                smtp.login(config["user"], config["password"])
            smtp.sendmail(config["from_address"], [recipient_email], msg.as_string())

        logger.info("[EmailService] Run summary notification sent to %s for project %s", recipient_email, project_id)
        return True
    except Exception as exc:
        logger.error("[EmailService] Failed to send run notification to %s: %s", recipient_email, exc)
        return False
