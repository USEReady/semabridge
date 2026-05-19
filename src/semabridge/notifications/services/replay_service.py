"""
Replay Service - allows re-processing of failed/dead notifications.
"""

import logging
from datetime import datetime
from typing import Optional, List
from uuid import UUID, uuid4
from sqlalchemy.orm import Session

from ..models import NotificationLog, NotificationEvent
from ..models.replay import ReplayFilter, ReplayResult, BulkReplayResult
from .notification_service import NotificationService

logger = logging.getLogger(__name__)


class ReplayService:
    """
    Service for replaying failed or dead-letter notifications.
    """
    
    def __init__(self, db_session: Session, notification_service: NotificationService):
        """
        Initialize replay service.
        
        Args:
            db_session: SQLAlchemy session
            notification_service: NotificationService for re-enqueueing
        """
        self.db_session = db_session
        self.notification_service = notification_service
        self.max_bulk_limit = 500
    
    async def replay_event(
        self,
        log_id: str,
        target_channel_ids: Optional[List[str]] = None,
    ) -> ReplayResult:
        """
        Replay a single notification event.
        
        Args:
            log_id: NotificationLog ID to replay
            target_channel_ids: If specified, only route to these channels (override original)
        
        Returns:
            ReplayResult with metadata
        """
        try:
            # Get the log entry
            log = self.db_session.query(NotificationLog).filter(
                NotificationLog.id == UUID(log_id)
            ).first()
            
            if not log:
                logger.error(f"Log not found: {log_id}")
                return ReplayResult(
                    log_id=log_id,
                    original_event_id="",
                    new_event_id="",
                    channels_targeted=[],
                )
            
            # Reconstruct event from log
            # Note: Full event data would need to be stored in log or accessed from archive
            # For now, create a minimal event with the available info
            new_event = NotificationEvent(
                id=uuid4(),
                correlation_id=getattr(log, "correlation_id", ""),
                sync_job_id=getattr(log, "sync_job_id", None),
                project_id=getattr(log, "project_id", None),
                type="notification_replay",
                level=getattr(log, "level", 0),
                title=getattr(log, "title", "Replayed notification"),
                message=getattr(log, "message", ""),
                payload={
                    "replayed_from_log_id": str(log.id),
                    **(getattr(log, "payload", {}) or {}),
                },
                source="replay",
                created_at=datetime.utcnow(),
            )
            
            # Add target channels if specified
            if target_channel_ids:
                new_event.payload["target_channels"] = target_channel_ids
            
            # Emit to queue (bypasses dedup by design, respects circuit breaker and quiet hours)
            success = await self.notification_service.emit(new_event)
            
            result = ReplayResult(
                log_id=log_id,
                original_event_id=str(log.event_id),
                new_event_id=str(new_event.id),
                channels_targeted=target_channel_ids or [],
                enqueued_at=datetime.utcnow() if success else None,
            )
            
            logger.info(f"Replayed event {log.event_id} -> {new_event.id}")
            return result
        
        except Exception as e:
            logger.error(f"Error replaying event {log_id}: {str(e)}", exc_info=True)
            return ReplayResult(
                log_id=log_id,
                original_event_id="",
                new_event_id="",
                channels_targeted=[],
            )
    
    async def replay_bulk(
        self,
        filter: ReplayFilter,
    ) -> BulkReplayResult:
        """
        Replay multiple notification events in bulk.
        
        Args:
            filter: Filter criteria for selecting logs to replay
        
        Returns:
            BulkReplayResult with counts and details
        """
        # Enforce limit
        limit = min(filter.limit, self.max_bulk_limit)
        
        # Get matching logs
        query = self.db_session.query(NotificationLog)
        
        if filter.status:
            query = query.filter(NotificationLog.status.in_(filter.status))
        
        if filter.channel_id:
            query = query.filter(NotificationLog.channel_id == UUID(filter.channel_id))
        
        if filter.since:
            query = query.filter(NotificationLog.created_at >= filter.since)
        
        if filter.until:
            query = query.filter(NotificationLog.created_at <= filter.until)
        
        logs = query.limit(limit).all()
        
        result = BulkReplayResult()
        
        # Process in batches of 50
        batch_size = 50
        for i in range(0, len(logs), batch_size):
            batch = logs[i:i + batch_size]
            
            for log in batch:
                try:
                    replay_result = await self.replay_event(str(log.id))
                    
                    if replay_result.enqueued_at:
                        result.replayed += 1
                        result.details.append(replay_result)
                    else:
                        result.errors += 1
                
                except Exception as e:
                    logger.error(f"Batch replay error for {log.id}: {str(e)}")
                    result.errors += 1
        
        result.skipped = len(logs) - result.replayed - result.errors
        
        logger.info(f"Bulk replay completed: {result.replayed} replayed, {result.errors} errors, {result.skipped} skipped")
        return result
    
    async def get_replay_candidates(
        self,
        filter: ReplayFilter,
    ) -> List[NotificationLog]:
        """
        Get a preview of logs that would be replayed.
        Does not actually replay anything.
        
        Args:
            filter: Filter criteria
        
        Returns:
            List of matching logs
        """
        # Enforce limit
        limit = min(filter.limit, self.max_bulk_limit)
        
        # Build query
        query = self.db_session.query(NotificationLog)
        
        if filter.status:
            query = query.filter(NotificationLog.status.in_(filter.status))
        
        if filter.channel_id:
            query = query.filter(NotificationLog.channel_id == UUID(filter.channel_id))
        
        if filter.since:
            query = query.filter(NotificationLog.created_at >= filter.since)
        
        if filter.until:
            query = query.filter(NotificationLog.created_at <= filter.until)
        
        logs = query.limit(limit).all()
        
        logger.info(f"Replay candidates query returned {len(logs)} logs")
        return logs
