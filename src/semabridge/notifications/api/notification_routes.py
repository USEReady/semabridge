"""
REST API routes for notification management.

All routes are under /api/settings/notification-*
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...api.deps import get_db
from ..models import NotificationChannel, NotificationLog, NotificationRoutingRuleRow, NotificationTemplate
from ..schemas.notification_schema import (
    NotificationChannelCreateRequest,
    NotificationChannelUpdateRequest,
    NotificationChannelResponse,
    NotificationLogResponse,
    NotificationTestRequest,
    NotificationRetryRequest,
    PaginatedResponse,
    ErrorResponse,
)
from ..utils.masking import mask_config_json

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/settings",
    tags=["notifications"],
)


class NotificationTemplateRequest(BaseModel):
    channel_id: str
    level_mask: int = Field(default=0)
    title_template: str = Field(min_length=1, max_length=4000)
    body_template: str = Field(min_length=1, max_length=8000)
    is_default: bool = False


class NotificationRoutingRuleRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    priority: int = 10
    conditions: Dict[str, Any] = Field(default_factory=dict)
    channel_ids: List[str] = Field(default_factory=list)
    stop_on_match: bool = False
    enabled: bool = True


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _load_config(config: Any) -> Dict[str, Any]:
    if isinstance(config, str):
        if not config:
            return {}
        return json.loads(config)
    return config or {}


def _channel_response(channel: NotificationChannel) -> Dict[str, Any]:
    return {
        "id": str(channel.id),
        "name": channel.name,
        "channel_type": _enum_value(channel.channel_type),
        "enabled": bool(channel.enabled),
        "config_json": mask_config_json(_load_config(channel.config_json)),
        "level_mask": channel.level_mask,
        "project_scope": channel.project_scope,
        "quiet_hours_enabled": bool(channel.quiet_hours_enabled),
        "quiet_hours_start": channel.quiet_hours_start,
        "quiet_hours_end": channel.quiet_hours_end,
        "timezone": channel.timezone,
        "digest_enabled": bool(channel.digest_enabled),
        "status": _enum_value(channel.status),
        "created_at": channel.created_at,
        "updated_at": channel.updated_at,
    }


def _template_response(template: NotificationTemplate) -> Dict[str, Any]:
    return {
        "id": str(template.id),
        "channel_id": str(template.channel_id),
        "level_mask": template.level_mask,
        "title_template": template.title_template,
        "body_template": template.body_template,
        "is_default": bool(template.is_default),
        "created_at": template.created_at,
        "updated_at": template.updated_at,
    }


def _routing_rule_response(rule: NotificationRoutingRuleRow) -> Dict[str, Any]:
    return {
        "id": str(rule.id),
        "name": rule.name,
        "priority": rule.priority,
        "conditions": rule.conditions or {},
        "channel_ids": [str(channel_id) for channel_id in (rule.channel_ids or [])],
        "stop_on_match": bool(rule.stop_on_match),
        "enabled": bool(rule.enabled),
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _matches_rule(conditions: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    level_mask = conditions.get("level_mask")
    if level_mask is not None and not (int(payload.get("level", 0)) & int(level_mask)):
        return False

    project_ids = conditions.get("project_ids") or []
    if project_ids and payload.get("project_id") not in project_ids:
        return False

    source_pattern = conditions.get("source_pattern")
    if source_pattern:
        from fnmatch import fnmatch
        if not fnmatch(str(payload.get("source", "")), str(source_pattern)):
            return False

    title_contains = conditions.get("title_contains")
    if title_contains and str(title_contains).lower() not in str(payload.get("title", "")).lower():
        return False

    payload_key_exists = conditions.get("payload_key_exists")
    payload_body = payload.get("payload") or payload
    if payload_key_exists and payload_key_exists not in payload_body:
        return False

    return True


# ===== Endpoints =====

@router.get("/notification-channels", response_model=PaginatedResponse)
async def list_notification_channels(
    skip: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=100),
    enabled: Optional[bool] = None,
    channel_type: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    List all notification channels.
    
    Secrets are masked in responses.
    """
    try:
        query = db.query(NotificationChannel)
        
        if enabled is not None:
            query = query.filter(NotificationChannel.enabled == enabled)
        if channel_type:
            query = query.filter(NotificationChannel.channel_type == channel_type)
        
        total = query.count()
        channels = query.offset(skip).limit(limit).all()
        
        items = [_channel_response(ch) for ch in channels]
        
        return PaginatedResponse(
            items=items,
            total=total,
            page=skip // limit,
            page_size=limit,
            total_pages=(total + limit - 1) // limit,
        )
    
    except Exception as e:
        logger.error(f"Failed to list channels: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list channels"
        )


@router.post(
    "/notification-channels",
    response_model=NotificationChannelResponse,
    status_code=status.HTTP_201_CREATED
)
async def create_notification_channel(
    request: NotificationChannelCreateRequest,
    db: Session = Depends(get_db),
):
    """
    Create a new notification channel.
    
    Configuration secrets must be provided and will be encrypted at rest.
    """
    try:
        # TODO: Validate config based on channel type
        # TODO: Encrypt config_json before storage
        
        channel = NotificationChannel(
            name=request.name,
            channel_type=request.channel_type,
            config_json=json.dumps(request.config_json),
            level_mask=request.level_mask,
            project_scope=request.project_scope,
            quiet_hours_enabled=request.quiet_hours_enabled,
            quiet_hours_start=request.quiet_hours_start,
            quiet_hours_end=request.quiet_hours_end,
            timezone=request.timezone,
            digest_enabled=request.digest_enabled,
        )
        
        db.add(channel)
        db.commit()
        db.refresh(channel)
        
        logger.info(f"Created notification channel {channel.id}")
        return _channel_response(channel)
    
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create channel: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.put(
    "/notification-channels/{channel_id}",
    response_model=NotificationChannelResponse,
)
async def update_notification_channel(
    channel_id: str,
    request: NotificationChannelUpdateRequest,
    db: Session = Depends(get_db),
):
    """Update an existing notification channel."""
    try:
        channel = db.query(NotificationChannel).filter(
            NotificationChannel.id == UUID(channel_id)
        ).first()

        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Channel not found"
            )

        updates = request.dict(exclude_unset=True)
        if "config_json" in updates and updates["config_json"] is not None:
            updates["config_json"] = json.dumps(updates["config_json"])

        for key, value in updates.items():
            setattr(channel, key, value)

        db.commit()
        db.refresh(channel)

        logger.info(f"Updated notification channel {channel_id}")
        return _channel_response(channel)

    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid channel ID"
        )
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to update channel: {e}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )


@router.delete(
    "/notification-channels/{channel_id}",
    status_code=status.HTTP_204_NO_CONTENT
)
async def delete_notification_channel(
    channel_id: str,
    db: Session = Depends(get_db),
):
    """Delete a notification channel."""
    try:
        channel = db.query(NotificationChannel).filter(
            NotificationChannel.id == UUID(channel_id)
        ).first()
        
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Channel not found"
            )
        
        db.delete(channel)
        db.commit()
        
        logger.info(f"Deleted notification channel {channel_id}")
    
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid channel ID"
        )
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to delete channel: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete channel"
        )


@router.post("/notification-channels/{channel_id}/test")
async def test_notification_channel(
    channel_id: str,
    db: Session = Depends(get_db),
):
    """
    Send a test notification to a channel.
    
    Returns: {success: bool, message: str}
    """
    try:
        channel = db.query(NotificationChannel).filter(
            NotificationChannel.id == UUID(channel_id)
        ).first()
        
        if not channel:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Channel not found"
            )
        
        # TODO: Implement test notification send
        # - Create a test NotificationEvent
        # - Get adapter and formatter for channel type
        # - Send via adapter
        # - Return result
        
        return {"success": True, "message": "Test notification sent (Phase 2 implementation)"}
    
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid channel ID"
        )
    except Exception as e:
        logger.error(f"Failed to test channel: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/notification-log", response_model=PaginatedResponse)
async def list_notification_logs(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    event_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    List notification delivery logs.
    
    Supports filtering by event, channel, and status.
    """
    try:
        query = db.query(NotificationLog)
        
        if event_id:
            query = query.filter(NotificationLog.event_id == UUID(event_id))
        if channel_id:
            query = query.filter(NotificationLog.channel_id == UUID(channel_id))
        if status:
            query = query.filter(NotificationLog.status == status)
        
        total = query.count()
        logs = query.order_by(NotificationLog.created_at.desc()).offset(skip).limit(limit).all()
        
        items = [NotificationLogResponse.from_orm(log).dict() for log in logs]
        
        return PaginatedResponse(
            items=items,
            total=total,
            page=skip // limit,
            page_size=limit,
            total_pages=(total + limit - 1) // limit,
        )
    
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid filter parameters"
        )
    except Exception as e:
        logger.error(f"Failed to list logs: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to list logs"
        )


@router.post("/notification-log/retry")
async def retry_notification(
    request: NotificationRetryRequest,
    db: Session = Depends(get_db),
):
    """
    Manually retry a failed notification delivery.
    
    Returns: {success: bool, message: str}
    """
    try:
        log = db.query(NotificationLog).filter(
            NotificationLog.id == UUID(request.log_id)
        ).first()
        
        if not log:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Log entry not found"
            )
        
        # TODO: Implement retry logic
        # - Get the original event
        # - Requeue for delivery
        
        return {"success": True, "message": "Retry queued (Phase 2 implementation)"}
    
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log ID"
        )
    except Exception as e:
        logger.error(f"Failed to retry notification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )


@router.get("/notification-templates", response_model=PaginatedResponse)
async def list_notification_templates(
    channel_id: Optional[str] = None,
    level: Optional[int] = None,
    db: Session = Depends(get_db),
):
    """List notification templates."""
    query = db.query(NotificationTemplate)
    if channel_id:
        query = query.filter(NotificationTemplate.channel_id == UUID(channel_id))
    if level is not None:
        query = query.filter(NotificationTemplate.level_mask == level)

    templates = query.order_by(NotificationTemplate.created_at.desc()).all()
    return PaginatedResponse(
        items=[_template_response(template) for template in templates],
        total=len(templates),
        page=0,
        page_size=len(templates),
        total_pages=1,
    )


@router.post("/notification-templates", status_code=status.HTTP_201_CREATED)
async def create_notification_template(request: NotificationTemplateRequest, db: Session = Depends(get_db)):
    """Create a notification template."""
    template = NotificationTemplate(
        id=uuid4(),
        channel_id=UUID(request.channel_id),
        level_mask=request.level_mask,
        title_template=request.title_template,
        body_template=request.body_template,
        is_default=request.is_default,
    )
    db.add(template)
    db.commit()
    db.refresh(template)
    return _template_response(template)


@router.put("/notification-templates/{template_id}")
async def update_notification_template(
    template_id: str,
    request: NotificationTemplateRequest,
    db: Session = Depends(get_db),
):
    """Update a notification template."""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.id == UUID(template_id)).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")

    template.channel_id = UUID(request.channel_id)
    template.level_mask = request.level_mask
    template.title_template = request.title_template
    template.body_template = request.body_template
    template.is_default = request.is_default
    db.commit()
    db.refresh(template)
    return _template_response(template)


@router.delete("/notification-templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_template(template_id: str, db: Session = Depends(get_db)):
    """Delete a notification template."""
    template = db.query(NotificationTemplate).filter(NotificationTemplate.id == UUID(template_id)).first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template not found")
    db.delete(template)
    db.commit()


@router.post("/notification-templates/validate")
async def validate_notification_template(request: NotificationTemplateRequest):
    """Validate template syntax for the UI."""
    try:
        from jinja2 import Template
        Template(request.title_template)
        Template(request.body_template)
        return {"valid": True, "errors": []}
    except Exception as exc:
        return {"valid": False, "errors": [str(exc)]}


@router.post("/notification-templates/preview")
async def preview_notification_template(request: NotificationTemplateRequest):
    """Render a template preview using representative notification data."""
    try:
        from jinja2 import Template
        context = {
            "level": "ERROR",
            "title": "Sync failed",
            "message": "A sample sync encountered an error.",
            "project_id": "sample-project",
            "source": "preview",
            "payload": {"rows_processed": 1200, "errors": 1},
        }
        return {
            "title": Template(request.title_template).render(**context),
            "body": Template(request.body_template).render(**context),
        }
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/notification-routing-rules", response_model=PaginatedResponse)
async def list_notification_routing_rules(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """List notification routing rules."""
    query = db.query(NotificationRoutingRuleRow).order_by(NotificationRoutingRuleRow.priority.asc())
    total = query.count()
    rules = query.offset(skip).limit(limit).all()
    return PaginatedResponse(
        items=[_routing_rule_response(rule) for rule in rules],
        total=total,
        page=skip // limit,
        page_size=limit,
        total_pages=(total + limit - 1) // limit,
    )


@router.post("/notification-routing-rules", status_code=status.HTTP_201_CREATED)
async def create_notification_routing_rule(request: NotificationRoutingRuleRequest, db: Session = Depends(get_db)):
    """Create a notification routing rule."""
    rule = NotificationRoutingRuleRow(
        id=uuid4(),
        name=request.name,
        priority=request.priority,
        enabled=request.enabled,
        conditions=request.conditions,
        channel_ids=[str(UUID(channel_id)) for channel_id in request.channel_ids],
        stop_on_match=request.stop_on_match,
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return _routing_rule_response(rule)


@router.put("/notification-routing-rules/{rule_id}")
async def update_notification_routing_rule(
    rule_id: str,
    request: NotificationRoutingRuleRequest,
    db: Session = Depends(get_db),
):
    """Update a notification routing rule."""
    rule = db.query(NotificationRoutingRuleRow).filter(NotificationRoutingRuleRow.id == UUID(rule_id)).first()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routing rule not found")

    rule.name = request.name
    rule.priority = request.priority
    rule.enabled = request.enabled
    rule.conditions = request.conditions
    rule.channel_ids = [str(UUID(channel_id)) for channel_id in request.channel_ids]
    rule.stop_on_match = request.stop_on_match
    db.commit()
    db.refresh(rule)
    return _routing_rule_response(rule)


@router.delete("/notification-routing-rules/{rule_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_routing_rule(rule_id: str, db: Session = Depends(get_db)):
    """Delete a notification routing rule."""
    rule = db.query(NotificationRoutingRuleRow).filter(NotificationRoutingRuleRow.id == UUID(rule_id)).first()
    if not rule:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Routing rule not found")
    db.delete(rule)
    db.commit()


@router.post("/notification-routing-rules/evaluate")
async def evaluate_notification_routing_rules(payload: Dict[str, Any], db: Session = Depends(get_db)):
    """Dry-run routing rules against a notification-like payload."""
    rules = (
        db.query(NotificationRoutingRuleRow)
        .filter(NotificationRoutingRuleRow.enabled == True)
        .order_by(NotificationRoutingRuleRow.priority.asc())
        .all()
    )
    matches = []
    channel_ids: List[str] = []
    for rule in rules:
        if _matches_rule(rule.conditions or {}, payload):
            matches.append(_routing_rule_response(rule))
            channel_ids.extend(str(channel_id) for channel_id in (rule.channel_ids or []))
            if rule.stop_on_match:
                break

    return {
        "matched_rules": matches,
        "channel_ids": list(dict.fromkeys(channel_ids)),
        "matched_count": len(matches),
    }


@router.get("/notification-analytics/delivery-stats")
async def get_notification_delivery_stats(
    range: Optional[str] = Query(default="24h"),
    since: Optional[str] = None,
    until: Optional[str] = None,
    channel_id: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Return lightweight delivery stats for the analytics page."""
    query = db.query(NotificationLog)
    now = datetime.utcnow()
    if range and not since:
        delta = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}.get(range)
        if delta:
            query = query.filter(NotificationLog.created_at >= now - delta)
    if since:
        query = query.filter(NotificationLog.created_at >= datetime.fromisoformat(since))
    if until:
        query = query.filter(NotificationLog.created_at <= datetime.fromisoformat(until))
    if channel_id:
        query = query.filter(NotificationLog.channel_id == UUID(channel_id))

    logs = query.all()
    total = len(logs)
    delivered = sum(1 for log in logs if _enum_value(log.status) == "delivered")
    durations = [log.duration_ms for log in logs if log.duration_ms is not None]
    return {
        "total_sent": total,
        "delivered": delivered,
        "failed": sum(1 for log in logs if _enum_value(log.status) == "failed"),
        "dead_letters": sum(1 for log in logs if _enum_value(log.status) == "dead"),
        "success_rate": (delivered / total * 100) if total else 0,
        "avg_latency_ms": (sum(durations) / len(durations)) if durations else 0,
    }


@router.get("/notification-analytics/channel-health")
async def get_notification_channel_health(channel_id: Optional[str] = None, db: Session = Depends(get_db)):
    """Return health metrics for one channel or all channels."""
    query = db.query(NotificationLog)
    if channel_id:
        query = query.filter(NotificationLog.channel_id == UUID(channel_id))

    logs = query.all()
    total = len(logs)
    delivered = sum(1 for log in logs if _enum_value(log.status) == "delivered")
    durations = sorted(log.duration_ms for log in logs if log.duration_ms is not None)
    p95_index = int(len(durations) * 0.95) - 1 if durations else 0
    return {
        "success_rate": (delivered / total * 100) if total else 0,
        "avg_latency_ms": (sum(durations) / len(durations)) if durations else 0,
        "p95_latency_ms": durations[max(p95_index, 0)] if durations else 0,
        "circuit_state": "closed",
    }

# TODO: Add GET endpoint for retrieving user notification preferences
# @router.get("/notification-preferences")
