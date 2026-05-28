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

    @property
    def name(self) -> str:
        return f"template-{self.id}"


class TemplateServiceEnvironment(SandboxedEnvironment):
    def is_safe_attribute(self, obj, attr, value):
        if attr in ("__class__", "__mro__", "__subclasses__", "__globals__", "__builtins__"):
            return False
        return super().is_safe_attribute(obj, attr, value)


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
        self.env = TemplateServiceEnvironment()
    
    async def get_template(
        self,
        db,
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
            db: SQLAlchemy Session
            channel_id: Channel UUID
            level: Notification level
        
        Returns:
            NotificationTemplate or None
        """
        try:
            from uuid import UUID
            from ..models import NotificationTemplate as DBTemplate
            
            # Query all templates for this channel
            templates = db.query(DBTemplate).filter(
                DBTemplate.channel_id == UUID(channel_id)
            ).all()
            
            if not templates:
                return None
                
            # Filter and prioritize matching templates
            matching = []
            for t in templates:
                # Check if template level_mask contains the event level (bitwise AND matches)
                if (t.level_mask & level) != 0:
                    is_exact = (t.level_mask == level)
                    is_default = t.is_default
                    # Sort key: (match_priority, exact_priority, default_priority)
                    # Lower values win:
                    # - match_priority: 0 for level match, 1 for default fallback
                    # - exact_priority: 0 for exact level mask match, 1 for subset range match
                    # - default_priority: 0 for non-default template, 1 for default template
                    priority = (0, 0 if is_exact else 1, 1 if is_default else 0)
                    matching.append((t, priority))
                elif t.is_default:
                    matching.append((t, (1, 1, 1)))
            
            if not matching:
                return None
                
            # Sort by priority tuple ascending
            matching.sort(key=lambda x: x[1])
            best_template = matching[0][0]
            
            # Convert DB model to TemplateService domain model
            return NotificationTemplate(
                id=str(best_template.id),
                channel_id=str(best_template.channel_id),
                level_mask=best_template.level_mask,
                title_template=best_template.title_template,
                body_template=best_template.body_template,
                is_default=best_template.is_default
            )
        except Exception as exc:
            logger.error(f"Failed to query notification template from DB: {exc}")
            return None
    
    def _verify_sandbox_safety(self, template_str: str) -> None:
        """
        Verify that a template string does not contain blocked/unsafe variables or attributes.
        Raises SecurityError if a dangerous name or attribute is found.
        """
        from jinja2.nodes import Name, Getattr
        from jinja2.sandbox import SecurityError
        
        try:
            ast = self.env.parse(template_str)
        except Exception:
            # Syntax errors will be handled during template compilation/rendering
            return
            
        blocked = {"__class__", "__mro__", "__subclasses__", "__globals__", "__builtins__"}
        
        for node in ast.find_all((Name, Getattr)):
            if isinstance(node, Name):
                if node.name in blocked or node.name.startswith("__"):
                    raise SecurityError(f"Access to variable {node.name!r} is blocked for security reasons.")
            elif isinstance(node, Getattr):
                if node.attr in blocked or node.attr.startswith("__"):
                    raise SecurityError(f"Access to attribute {node.attr!r} is blocked for security reasons.")

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
        from jinja2.sandbox import SecurityError

        try:
            # Verify sandbox safety first
            self._verify_sandbox_safety(template.title_template)
            self._verify_sandbox_safety(template.body_template)

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
        
        except TemplateSyntaxError as e:
            error_msg = f"Template syntax error: {str(e)}"
            logger.error(error_msg)
            raise TemplateRenderError(error_msg) from e
        except SecurityError as e:
            error_msg = f"Security error: {str(e)}"
            logger.error(error_msg)
            raise TemplateRenderError(error_msg) from e
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
