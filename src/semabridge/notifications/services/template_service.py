"""
Template Service - render Jinja2 templates with sandboxing and variable substitution.
"""

import logging
import os
from typing import Optional, List, Tuple
from jinja2 import Template, TemplateSyntaxError, TemplateError
from jinja2.sandbox import SandboxedEnvironment

from ..models import NotificationEvent

logger = logging.getLogger(__name__)


class TemplateRenderError(Exception):
    """Error rendering template."""
    pass


class NotificationTemplate:
    """Simple template model (would map to DB in production)."""
    
    def __init__(
        self,
        id: str,
        channel_id: str,
        level_mask: int,
        title_template: str,
        body_template: str,
        is_default: bool = False,
    ):
        self.id = id
        self.channel_id = channel_id
        self.level_mask = level_mask
        self.title_template = title_template
        self.body_template = body_template
        self.is_default = is_default


class TemplateService:
    """
    Service for managing and rendering notification templates.
    Uses Jinja2 with sandbox for safe template execution.
    """
    
    MAX_TEMPLATE_LENGTH = int(os.getenv("TEMPLATE_MAX_LENGTH_CHARS", "4000"))
    MAX_RENDERED_LENGTH = int(os.getenv("TEMPLATE_MAX_RENDERED_CHARS", "8000"))
    
    def __init__(self):
        """Initialize template service with sandboxed Jinja2 environment."""
        # Create sandboxed environment
        self.env = SandboxedEnvironment(
            # Restrict access to dangerous attributes
            restricted_access=["__class__", "__mro__", "__subclasses__", "__globals__", "__builtins__"],
        )
    
    async def get_template(
        self,
        channel_id: str,
        level: int,
    ) -> Optional[NotificationTemplate]:
        """
        Get the best matching template for a channel and level.
        
        Priority:
        1. Exact level match
        2. is_default=True for channel
        3. None (use formatter defaults)
        
        Args:
            channel_id: Channel UUID
            level: Notification level
        
        Returns:
            NotificationTemplate or None
        """
        # In production, would query DB:
        # SELECT * FROM notification_templates
        # WHERE channel_id = ? AND (level_mask & ? OR is_default)
        # ORDER BY is_default ASC, level_mask DESC
        # LIMIT 1
        
        # For now, return None (formatters use built-in defaults)
        return None
    
    def render(
        self,
        template: NotificationTemplate,
        event: NotificationEvent,
    ) -> Tuple[str, str]:
        """
        Render title and body templates.
        
        Available variables:
        - title, message, level, level_str
        - project_id, sync_job_id, correlation_id
        - source, created_at
        - payload.<key> (nested access)
        
        Args:
            template: NotificationTemplate to render
            event: Event to use for rendering
        
        Returns:
            (rendered_title, rendered_body)
        
        Raises:
            TemplateRenderError on rendering failure
        """
        try:
            # Prepare context
            context = self._build_context(event)
            
            # Render templates
            title_tmpl = self.env.from_string(template.title_template)
            body_tmpl = self.env.from_string(template.body_template)
            
            rendered_title = title_tmpl.render(**context)
            rendered_body = body_tmpl.render(**context)
            
            # Enforce max length
            if len(rendered_title) > self.MAX_RENDERED_LENGTH:
                logger.warning(f"Template title exceeds max length: {len(rendered_title)}")
                rendered_title = rendered_title[:self.MAX_RENDERED_LENGTH]
            
            if len(rendered_body) > self.MAX_RENDERED_LENGTH:
                logger.warning(f"Template body exceeds max length: {len(rendered_body)}")
                rendered_body = rendered_body[:self.MAX_RENDERED_LENGTH]
            
            return rendered_title, rendered_body
        
        except TemplateError as e:
            error_msg = f"Template render error: {str(e)}"
            logger.error(error_msg)
            raise TemplateRenderError(error_msg) from e
    
    async def validate_template(
        self,
        title_template: str,
        body_template: str,
    ) -> List[str]:
        """
        Validate template syntax and render-ability.
        
        Args:
            title_template: Title template string
            body_template: Body template string
        
        Returns:
            List of validation errors (empty = valid)
        """
        errors = []
        
        # Check length
        if len(title_template) > self.MAX_TEMPLATE_LENGTH:
            errors.append(f"Title template exceeds max length ({self.MAX_TEMPLATE_LENGTH})")
        
        if len(body_template) > self.MAX_TEMPLATE_LENGTH:
            errors.append(f"Body template exceeds max length ({self.MAX_TEMPLATE_LENGTH})")
        
        # Dry-run render with dummy event
        try:
            dummy_event = NotificationEvent(
                id="dummy-id",
                correlation_id="dummy-corr",
                title="Test",
                message="Test message",
                level=16,  # INFO
                source="test",
            )
            
            self.render(
                NotificationTemplate(
                    id="dummy",
                    channel_id="dummy",
                    level_mask=63,
                    title_template=title_template,
                    body_template=body_template,
                ),
                dummy_event,
            )
        
        except TemplateRenderError as e:
            errors.append(str(e))
        
        except Exception as e:
            errors.append(f"Unexpected error: {str(e)}")
        
        return errors
    
    @staticmethod
    def _build_context(event: NotificationEvent) -> dict:
        """
        Build Jinja2 context from event.
        
        Args:
            event: Notification event
        
        Returns:
            Context dict for template rendering
        """
        from ..constants import level_to_string
        
        return {
            "title": event.title,
            "message": event.message,
            "level": event.level,
            "level_str": level_to_string(event.level),
            "project_id": event.project_id,
            "sync_job_id": event.sync_job_id,
            "correlation_id": event.correlation_id,
            "source": event.source,
            "created_at": event.created_at.isoformat() if event.created_at else "",
            "type": event.type,
            "payload": event.payload,  # Allows {{ payload.key }} access
        }
