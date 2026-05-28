"""
Email formatter for HTML and plaintext notifications.
"""

from typing import Dict, Any
from html import escape
from ..models import NotificationEvent
from ..constants import level_to_string
from .base import BaseFormatter


class EmailFormatter(BaseFormatter):
    """
    Format NotificationEvent as HTML email with plaintext fallback.
    """
    
    def format(
        self,
        event: NotificationEvent,
        channel_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Format event as HTML email.
        
        Args:
            event: Notification event
            channel_config: Email channel config
        
        Returns:
            Email payload with html and plaintext variants
        """
        # HTML version
        html_content = self._build_html(event, channel_config)
        
        # Plaintext version
        plaintext_content = self._build_plaintext(event, channel_config)
        
        # Build base response
        res = {
            "subject": f"[{level_to_string(event.level)}] {event.title}",
            "html": html_content,
            "plaintext": plaintext_content,
            "from_address": channel_config.get("from_address", "noreply@semabridge.local"),
            "to_addresses": channel_config.get("to_addresses", []),
        }
        
        # STEP 1 — Verify enriched payload reaches email adapter
        # Copy payload keys directly to the payload dictionary
        if event.payload:
            for k, v in event.payload.items():
                res[k] = v
                
        return res
    
    def _build_html(self, event: NotificationEvent, channel_config: Dict[str, Any]) -> str:
        """Build HTML email content."""
        level_name = level_to_string(event.level)
        escaped_title = escape(event.title)
        
        # Extract variables with safe defaults (STEP 5)
        payload = event.payload or {}
        
        project = payload.get("project_id") or payload.get("project") or event.project_id or "Unknown Project"
        sync_mode = payload.get("sync_mode") or payload.get("mode") or "copy"
        source_platform = payload.get("source_platform") or "Unknown Source"
        target_platform = payload.get("target_platform") or "Unknown Target"
        
        datasets_val = payload.get("datasets") or payload.get("dataset_count")
        metrics_val = payload.get("metrics") or payload.get("metric_count")
        
        datasets = f"{datasets_val} datasets" if datasets_val is not None else "0 datasets"
        metrics = f"{metrics_val} metrics" if metrics_val is not None else "0 metrics"
        
        run_id = payload.get("run_id") or event.sync_job_id or "unknown_run"
        
        # Check if the message itself is a templated or raw message
        if event.message.startswith("Synchronization completed successfully"):
            message_body = "Synchronization completed successfully."
        else:
            message_body = event.message
            
        escaped_message = escape(message_body).replace("\n", "<br>")
        
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, sans-serif; background-color: #f8fafc; color: #0f172a; margin: 0; padding: 0; }}
        .container {{ max-width: 600px; margin: 20px auto; padding: 24px; background-color: #ffffff; border: 1px solid #e2e8f0; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05); }}
        .header {{ background: linear-gradient(135deg, #1e293b 0%, #0f172a 100%); color: #ffffff; padding: 24px; border-radius: 8px; margin-bottom: 24px; }}
        .title {{ font-size: 22px; font-weight: 700; margin: 0; }}
        .level {{ font-size: 11px; font-weight: 600; text-transform: uppercase; margin-top: 6px; letter-spacing: 0.05em; color: #94a3b8; }}
        .content {{ font-size: 15px; line-height: 1.6; color: #334155; margin: 20px 0; }}
        
        /* Modern Metadata Card */
        .metadata-card {{
            background-color: #f8fafc;
            border: 1px solid #e2e8f0;
            border-radius: 8px;
            padding: 20px;
            margin: 24px 0;
        }}
        .metadata-header {{
            font-size: 13px;
            font-weight: 700;
            color: #64748b;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            margin-bottom: 12px;
            border-bottom: 1px solid #e2e8f0;
            padding-bottom: 6px;
        }}
        .metadata-row {{
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid #f1f5f9;
            font-size: 14px;
        }}
        .metadata-row:last-child {{
            border-bottom: none;
        }}
        .metadata-label {{
            font-weight: 600;
            color: #475569;
        }}
        .metadata-value {{
            color: #0f172a;
            font-weight: 700;
        }}
        .metadata-code {{
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 12px;
            background-color: #e2e8f0;
            padding: 2px 6px;
            border-radius: 4px;
            color: #0f172a;
        }}
        
        .footer {{ font-size: 12px; color: #64748b; margin-top: 24px; border-top: 1px solid #e2e8f0; padding-top: 16px; text-align: center; }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <div class="title">{escaped_title}</div>
            <div class="level">{level_name}</div>
        </div>
        <div class="content">
            {escaped_message}
        </div>
        
        <div class="metadata-card">
            <div class="metadata-header">📌 Operational Summary</div>
            <div class="metadata-row">
                <span class="metadata-label">Project</span>
                <span class="metadata-value">{escape(project)}</span>
            </div>
            <div class="metadata-row">
                <span class="metadata-label">🔄 Mode</span>
                <span class="metadata-value">{escape(sync_mode)}</span>
            </div>
            <div class="metadata-row">
                <span class="metadata-label">⬅️ Source</span>
                <span class="metadata-value">{escape(source_platform)}</span>
            </div>
            <div class="metadata-row">
                <span class="metadata-label">➡️ Target</span>
                <span class="metadata-value">{escape(target_platform)}</span>
            </div>
            <div class="metadata-row">
                <span class="metadata-label">📊 Datasets</span>
                <span class="metadata-value">{escape(datasets)}</span>
            </div>
            <div class="metadata-row">
                <span class="metadata-label">📈 Metrics</span>
                <span class="metadata-value">{escape(metrics)}</span>
            </div>
            <div class="metadata-row" style="margin-top: 8px; border-top: 1px solid #e2e8f0; padding-top: 12px;">
                <span class="metadata-label">🆔 Run ID</span>
                <span class="metadata-code">{escape(run_id)}</span>
            </div>
        </div>
"""
        
        # Add correlation_id in footer if it exists
        if event.correlation_id:
            html += f"""
            <div class='metadata-row' style='font-size: 12px; color: #64748b;'>
                <span>Correlation ID:</span>
                <span>{escape(event.correlation_id)}</span>
            </div>
            """
            
        html += f"""
        <div class="footer">
            <p>Created: {event.created_at.isoformat() if event.created_at else ""}</p>
            <p>This is an automated notification from SemaBridge.</p>
        </div>
    </div>
</body>
</html>
"""
        return html
    
    def _build_plaintext(self, event: NotificationEvent, channel_config: Dict[str, Any]) -> str:
        """Build plaintext email content."""
        level_name = level_to_string(event.level)
        
        # Extract variables with safe defaults (STEP 5)
        payload = event.payload or {}
        
        project = payload.get("project_id") or payload.get("project") or event.project_id or "Unknown Project"
        sync_mode = payload.get("sync_mode") or payload.get("mode") or "copy"
        source_platform = payload.get("source_platform") or "Unknown Source"
        target_platform = payload.get("target_platform") or "Unknown Target"
        
        datasets_val = payload.get("datasets") or payload.get("dataset_count")
        metrics_val = payload.get("metrics") or payload.get("metric_count")
        
        datasets = str(datasets_val) if datasets_val is not None else "0"
        metrics = str(metrics_val) if metrics_val is not None else "0"
        
        run_id = payload.get("run_id") or event.sync_job_id or "unknown_run"
        
        # Check if the message itself is a templated or raw message
        if event.message.startswith("Synchronization completed successfully"):
            message_body = "Synchronization completed successfully."
        else:
            message_body = event.message
            
        lines = [
            message_body,
            "",
            "━━━━━━━━━━━━━━",
            f"📌 Project: {project}",
            f"🔄 Mode: {sync_mode}",
            "",
            f"⬅️ Source: {source_platform}",
            f"➡️ Target: {target_platform}",
            "",
            f"📊 Datasets: {datasets}",
            f"📈 Metrics: {metrics}",
            "",
            f"🆔 Run ID: {run_id}",
        ]
        
        if event.correlation_id:
            lines.extend(["", f"Correlation ID: {event.correlation_id}"])
        
        lines.extend([
            "",
            f"Created: {event.created_at.isoformat() if event.created_at else ''}",
            "",
            "---",
            "This is an automated notification from SemaBridge.",
        ])
        
        return "\n".join(lines)
