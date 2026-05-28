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
from pydantic import BaseModel, Field, ValidationError
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

from ..utils.crypto import NotificationCrypto
crypto = NotificationCrypto()

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


class NotificationTemplatePreviewRequest(BaseModel):
    channel_id: Optional[str] = ""
    level_mask: Optional[int] = 0
    title_template: Optional[str] = ""
    body_template: Optional[str] = ""
    is_default: Optional[bool] = False
    sample_context: Optional[Dict[str, Any]] = None


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
    try:
        decrypted_config = crypto.decrypt(channel.config_json)
    except Exception as e:
        logger.error(f"Failed to decrypt channel config: {e}")
        decrypted_config = channel.config_json

    return {
        "id": str(channel.id),
        "name": channel.name,
        "channel_type": _enum_value(channel.channel_type),
        "enabled": bool(channel.enabled),
        "config_json": mask_config_json(_load_config(decrypted_config)),
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
        from ..utils.validators import (
            validate_slack_webhook,
            validate_teams_webhook,
            validate_webhook_url,
            validate_smtp_config,
            validate_pagerduty_config,
        )

        channel_type = _enum_value(request.channel_type)
        config_dict = request.config_json or {}

        # Diagnostic: log raw incoming payload for troubleshooting schema mismatches
        logger.warning({
            "event": "create_channel_incoming_payload",
            "channel_type": channel_type,
            "config_keys": list(config_dict.keys()),
        })

        if channel_type == "slack":
            is_valid, err = validate_slack_webhook(config_dict.get("webhook_url", ""))
            if not is_valid:
                logger.warning({"event": "channel_validation_failure", "channel_type": "slack", "error": err})
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        elif channel_type == "teams":
            is_valid, err = validate_teams_webhook(config_dict.get("webhook_url", ""))
            if not is_valid:
                logger.warning({"event": "channel_validation_failure", "channel_type": "teams", "error": err})
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        elif channel_type == "webhook":
            is_valid, err = validate_webhook_url(config_dict.get("webhook_url", ""))
            if not is_valid:
                logger.warning({"event": "channel_validation_failure", "channel_type": "webhook", "error": err})
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        elif channel_type == "email":
            is_valid, err = validate_smtp_config(config_dict)
            if not is_valid:
                logger.warning({"event": "channel_validation_failure", "channel_type": "email", "error": err})
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        elif channel_type == "pagerduty":
            is_valid, err = validate_pagerduty_config(config_dict)
            if not is_valid:
                logger.warning({"event": "channel_validation_failure", "channel_type": "pagerduty", "error": err})
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
        elif channel_type == "snowflake":
            required_keys = ["account", "user", "password", "warehouse", "database", "schema", "table"]
            for key in required_keys:
                if not config_dict.get(key):
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Missing or empty '{key}' for Snowflake channel"
                    )

        encrypted_config = crypto.encrypt(json.dumps(request.config_json))
        
        channel = NotificationChannel(
            name=request.name,
            channel_type=request.channel_type,
            config_json=encrypted_config,
            level_mask=request.level_mask,
            enabled=request.enabled,
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
    
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to create channel: {e}", exc_info=True)
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
            from ..utils.validators import (
                validate_slack_webhook,
                validate_teams_webhook,
                validate_webhook_url,
                validate_smtp_config,
                validate_pagerduty_config,
            )

            channel_type = _enum_value(channel.channel_type)
            config_dict = updates["config_json"] or {}

            if channel_type == "slack":
                is_valid, err = validate_slack_webhook(config_dict.get("webhook_url", ""))
                if not is_valid:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            elif channel_type == "teams":
                is_valid, err = validate_teams_webhook(config_dict.get("webhook_url", ""))
                if not is_valid:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            elif channel_type == "webhook":
                is_valid, err = validate_webhook_url(config_dict.get("webhook_url", ""))
                if not is_valid:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            elif channel_type == "email":
                is_valid, err = validate_smtp_config(config_dict)
                if not is_valid:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            elif channel_type == "pagerduty":
                is_valid, err = validate_pagerduty_config(config_dict)
                if not is_valid:
                    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=err)
            elif channel_type == "snowflake":
                required_keys = ["account", "user", "password", "warehouse", "database", "schema", "table"]
                for key in required_keys:
                    if not config_dict.get(key):
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Missing or empty '{key}' for Snowflake channel"
                        )

            updates["config_json"] = crypto.encrypt(json.dumps(updates["config_json"]))

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
    
    Returns: Real delivery results including latency and response info.
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
        
        # Get adapter and formatter classes
        from ..adapters import SlackAdapter, EmailAdapter, WebhookAdapter, TeamsAdapter, PagerDutyAdapter, SnowflakeAdapter
        from ..formatters import SlackFormatter, EmailFormatter, WebhookFormatter, TeamsFormatter, PagerDutyFormatter, SnowflakeFormatter
        
        ADAPTERS = {
            "slack": SlackAdapter,
            "email": EmailAdapter,
            "webhook": WebhookAdapter,
            "teams": TeamsAdapter,
            "pagerduty": PagerDutyAdapter,
            "snowflake": SnowflakeAdapter,
        }
        FORMATTERS = {
            "slack": SlackFormatter,
            "email": EmailFormatter,
            "webhook": WebhookFormatter,
            "teams": TeamsFormatter,
            "pagerduty": PagerDutyFormatter,
            "snowflake": SnowflakeFormatter,
        }
        
        channel_type = _enum_value(channel.channel_type)
        adapter_class = ADAPTERS.get(channel_type)
        formatter_class = FORMATTERS.get(channel_type)
        
        if not adapter_class or not formatter_class:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported channel type: {channel_type}"
            )
            
        # Decrypt config_json at runtime
        try:
            decrypted_config = crypto.decrypt(channel.config_json)
            config_json = json.loads(decrypted_config)
        except Exception as decrypt_err:
            logger.error(f"Failed to decrypt/parse config for channel test: {decrypt_err}")
            config_json = {}
            
        # Create a test NotificationEvent
        from ..models import NotificationEvent
        
        test_event = NotificationEvent(
            id=uuid4(),
            correlation_id=f"test_{str(uuid4())[:8]}",
            sync_job_id="test_job_123",
            project_id=channel.project_scope or "test_project",
            type="api_test",
            level=16,  # INFO
            title=f"SemaBridge Channel Test: {channel.name}",
            message="This is a test notification generated from the SemaBridge Channel Configuration Management page. Your channel integration is configured correctly!",
            payload={"test": True, "initiated_at": datetime.utcnow().isoformat()},
            source="management_api",
            created_at=datetime.utcnow()
        )
        
        # Format the event
        formatter = formatter_class()
        formatted_payload = formatter.format(test_event, config_json)
        
        # Send using the adapter
        adapter = adapter_class(config_json)
        start_time = datetime.utcnow()
        try:
            result = await adapter.send(formatted_payload, config_json)
        finally:
            await adapter.close()
            
        end_time = datetime.utcnow()
        latency_ms = int((end_time - start_time).total_seconds() * 1000)
        
        # Log this attempt to DB for auditing
        from ..models import NotificationLog, DeliveryStatusEnum
        try:
            status_val = "delivered" if result.get("success") else "failed"
            log_entry = NotificationLog(
                id=uuid4(),
                event_id=test_event.id,
                channel_id=channel.id,
                status=DeliveryStatusEnum(status_val),
                attempt=1,
                response_code=result.get("response_code"),
                response_body=str(result.get("response_body", ""))[:1000] if result.get("response_body") else None,
                duration_ms=latency_ms,
                error_message=result.get("error")[:500] if result.get("error") else None,
                created_at=datetime.utcnow()
            )
            db.add(log_entry)
            db.commit()
        except Exception as log_err:
            logger.error(f"Failed to log test channel delivery: {log_err}")
            db.rollback()
            
        if not result.get("success"):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"{channel_type.capitalize()} delivery failed: {result.get('error')}"
            )
            
        return {
            "success": True,
            "message": "Test notification sent successfully!",
            "latency_ms": latency_ms,
            "response_code": result.get("response_code"),
            "response_body": result.get("response_body"),
        }
    
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
    since: Optional[str] = None,
    until: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    List notification delivery logs.
    
    Supports filtering by event, channel, status, since, and until dates.
    """
    import traceback
    from datetime import time as datetime_time
    from fastapi import status as fastapi_status
    
    logger.info(
        f"Querying notification logs: skip={skip}, limit={limit}, "
        f"event_id={event_id}, channel_id={channel_id}, status={status}, "
        f"since={since}, until={until}"
    )
    
    try:
        query = db.query(NotificationLog)
        
        if event_id:
            try:
                query = query.filter(NotificationLog.event_id == UUID(event_id))
            except ValueError as val_err:
                logger.error(f"Invalid UUID for event_id '{event_id}': {val_err}")
                raise HTTPException(
                    status_code=fastapi_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid event_id UUID: {event_id}"
                )
                
        if channel_id:
            try:
                query = query.filter(NotificationLog.channel_id == UUID(channel_id))
            except ValueError as val_err:
                logger.error(f"Invalid UUID for channel_id '{channel_id}': {val_err}")
                raise HTTPException(
                    status_code=fastapi_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid channel_id UUID: {channel_id}"
                )
                
        if status:
            query = query.filter(NotificationLog.status == status)
            
        if since:
            try:
                if len(since) == 10:
                    since_dt = datetime.combine(datetime.fromisoformat(since).date(), datetime_time.min)
                else:
                    since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
                query = query.filter(NotificationLog.created_at >= since_dt)
            except ValueError as e:
                logger.error(f"Invalid date for since '{since}': {e}")
                raise HTTPException(
                    status_code=fastapi_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid since date format (use YYYY-MM-DD or ISO 8601): {since}"
                )
                
        if until:
            try:
                if len(until) == 10:
                    until_dt = datetime.combine(datetime.fromisoformat(until).date(), datetime_time.max)
                else:
                    until_dt = datetime.fromisoformat(until.replace("Z", "+00:00"))
                query = query.filter(NotificationLog.created_at <= until_dt)
            except ValueError as e:
                logger.error(f"Invalid date for until '{until}': {e}")
                raise HTTPException(
                    status_code=fastapi_status.HTTP_400_BAD_REQUEST,
                    detail=f"Invalid until date format (use YYYY-MM-DD or ISO 8601): {until}"
                )
        
        total = query.count()
        logs = query.order_by(NotificationLog.created_at.desc()).offset(skip).limit(limit).all()
        
        # Serialize manually to prevent ValidationError with UUIDs or Enums
        items = []
        for log in logs:
            try:
                items.append({
                    "id": str(log.id),
                    "event_id": str(log.event_id),
                    "channel_id": str(log.channel_id),
                    "status": _enum_value(log.status),
                    "attempt": log.attempt,
                    "response_code": log.response_code,
                    "response_body": log.response_body,
                    "duration_ms": log.duration_ms,
                    "error_message": log.error_message,
                    "created_at": log.created_at,
                })
            except Exception as ser_err:
                logger.error(f"Failed to serialize log {getattr(log, 'id', 'unknown')}: {ser_err}")
                logger.error(traceback.format_exc())
                raise
        
        return PaginatedResponse(
            items=items,
            total=total,
            page=skip // limit if limit > 0 else 0,
            page_size=limit,
            total_pages=(total + limit - 1) // limit if limit > 0 else 1,
        )
    
    except HTTPException:
        raise
    except ValueError as e:
        logger.error(f"ValueError in list_notification_logs: {e}")
        logger.error(traceback.format_exc())
        raise HTTPException(
            status_code=fastapi_status.HTTP_400_BAD_REQUEST,
            detail="Invalid filter parameters"
        )
    except Exception as e:
        tb = traceback.format_exc()
        logger.error(f"Failed to list logs: {e}")
        logger.error(tb)
        raise HTTPException(
            status_code=fastapi_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to list logs due to backend exception: {str(e)}\nTraceback:\n{tb}"
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
        
        import os
        from ..services.notification_service import NotificationService
        from ..services.replay_service import ReplayService
        
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
        notification_service = NotificationService(redis_url)
        replay_service = ReplayService(db, notification_service)
        
        replay_result = await replay_service.replay_event(str(log.id))
        
        if replay_result.enqueued_at:
            return {
                "success": True,
                "message": f"Retry queued successfully (new event: {replay_result.new_event_id})"
            }
        else:
            return {"success": False, "message": "Failed to queue retry"}
    
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid log ID"
        )
    except HTTPException:
        raise
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


FALLBACK_CONTEXT = {
    "title": "Fabric Sync Failed",
    "message": "Warehouse timeout occurred",
    "level": 40,
    "level_str": "ERROR",
    "project_id": "fabric-prod",
    "sync_job_id": "job-123",
    "source": "sync_engine",
    "payload": {
        "warehouse": "fabric-east"
    }
}


@router.post("/notification-templates/preview")
async def preview_notification_template(request_data: Dict[str, Any]):
    """Render a template preview using representative notification data."""
    logger.info("Template preview request received", extra={"payload": request_data})
    
    try:
        request = NotificationTemplatePreviewRequest.model_validate(request_data)
    except ValidationError as err:
        errors = err.errors()
        missing_fields = []
        invalid_schema_paths = []
        explanations = []
        for e in errors:
            loc_path = " -> ".join(str(l) for l in e["loc"])
            invalid_schema_paths.append(loc_path)
            if e["type"] == "missing":
                missing_fields.append(str(e["loc"][-1]))
            explanations.append(f"Field '{loc_path}': {e['msg']}")
        
        detail_msg = "; ".join(explanations)
        logger.warning(
            "Template preview validation failure",
            extra={
                "payload": request_data,
                "missing_fields": missing_fields,
                "invalid_schema_paths": invalid_schema_paths,
                "errors": errors,
            }
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": f"Validation failed: {detail_msg}",
                "missing_fields": missing_fields,
                "invalid_schema_paths": invalid_schema_paths,
                "errors": errors
            }
        )

    context = dict(FALLBACK_CONTEXT)
    if request.sample_context:
        context.update(request.sample_context)

    title_tmpl = request.title_template or ""
    body_tmpl = request.body_template or ""

    try:
        from jinja2 import Template
        rendered_title = Template(title_tmpl).render(**context)
        rendered_body = Template(body_tmpl).render(**context)
        return {
            "title": rendered_title,
            "body": rendered_body,
        }
    except Exception as exc:
        logger.error(
            "Template rendering exception",
            extra={
                "title_template": title_tmpl,
                "body_template": body_tmpl,
                "context": context,
                "error": str(exc),
            },
            exc_info=True
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Template rendering failed: {str(exc)}"
        )


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
