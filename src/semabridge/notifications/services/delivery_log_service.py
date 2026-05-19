"""
Delivery logging service for audit trails and analytics.
"""

import logging
from datetime import datetime
from typing import List, Dict, Any, Optional
from uuid import UUID

from sqlalchemy.orm import Session

from ..models import NotificationLog, NotificationChannel, DeliveryStatusEnum
from ...constants import NotificationStatus

logger = logging.getLogger(__name__)


class DeliveryLogService:
    """
    Log all notification delivery attempts for audit and analytics.
    """
    
    def __init__(self, db_session: Session):
        """
        Initialize delivery log service.
        
        Args:
            db_session: SQLAlchemy session
        """
        self.db = db_session
    
    async def log_delivery(
        self,
        event_id: UUID,
        channel_id: UUID,
        status: str,
        attempt: int = 1,
        response_code: Optional[int] = None,
        response_body: Optional[str] = None,
        duration_ms: Optional[int] = None,
        error_message: Optional[str] = None,
    ) -> bool:
        """
        Log a delivery attempt.
        
        Args:
            event_id: Event ID
            channel_id: Channel ID
            status: Delivery status (from NotificationStatus)
            attempt: Attempt number
            response_code: HTTP response code if applicable
            response_body: Response body if applicable
            duration_ms: Duration in milliseconds
            error_message: Error message if failed
        
        Returns:
            True if logged successfully
        """
        try:
            log_entry = NotificationLog(
                event_id=event_id,
                channel_id=channel_id,
                status=DeliveryStatusEnum(status),
                attempt=attempt,
                response_code=response_code,
                response_body=response_body[:1000] if response_body else None,  # Truncate
                duration_ms=duration_ms,
                error_message=error_message[:500] if error_message else None,  # Truncate
            )
            
            self.db.add(log_entry)
            self.db.commit()
            
            logger.debug(f"Logged delivery: {event_id} -> {channel_id}: {status}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to log delivery: {e}")
            self.db.rollback()
            return False
    
    async def get_logs(
        self,
        event_id: Optional[UUID] = None,
        channel_id: Optional[UUID] = None,
        status: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """
        Query delivery logs.
        
        Args:
            event_id: Filter by event ID
            channel_id: Filter by channel ID
            status: Filter by status
            limit: Result limit
            offset: Result offset
        
        Returns:
            List of log dicts
        """
        try:
            query = self.db.query(NotificationLog)
            
            if event_id:
                query = query.filter(NotificationLog.event_id == event_id)
            if channel_id:
                query = query.filter(NotificationLog.channel_id == channel_id)
            if status:
                query = query.filter(NotificationLog.status == DeliveryStatusEnum(status))
            
            logs = query.order_by(NotificationLog.created_at.desc()).limit(limit).offset(offset).all()
            
            return [
                {
                    "id": str(log.id),
                    "event_id": str(log.event_id),
                    "channel_id": str(log.channel_id),
                    "status": log.status.value,
                    "attempt": log.attempt,
                    "response_code": log.response_code,
                    "response_body": log.response_body,
                    "duration_ms": log.duration_ms,
                    "error_message": log.error_message,
                    "created_at": log.created_at.isoformat(),
                }
                for log in logs
            ]
        
        except Exception as e:
            logger.error(f"Failed to query delivery logs: {e}")
            return []
    
    async def get_channel_status(self, channel_id: UUID) -> Dict[str, Any]:
        """
        Get delivery statistics for a channel.
        
        Args:
            channel_id: Channel ID
        
        Returns:
            Statistics dict
        """
        try:
            query = self.db.query(NotificationLog).filter(
                NotificationLog.channel_id == channel_id
            )
            
            delivered = query.filter(NotificationLog.status == DeliveryStatusEnum.DELIVERED).count()
            failed = query.filter(NotificationLog.status == DeliveryStatusEnum.FAILED).count()
            dead = query.filter(NotificationLog.status == DeliveryStatusEnum.DEAD).count()
            total = query.count()
            
            avg_duration = self.db.query(
                func.avg(NotificationLog.duration_ms)
            ).filter(NotificationLog.channel_id == channel_id).scalar()
            
            return {
                "channel_id": str(channel_id),
                "total_attempts": total,
                "delivered": delivered,
                "failed": failed,
                "dead": dead,
                "success_rate": (delivered / total * 100) if total > 0 else 0,
                "avg_duration_ms": int(avg_duration) if avg_duration else 0,
            }
        
        except Exception as e:
            logger.error(f"Failed to get channel status: {e}")
            return {}
