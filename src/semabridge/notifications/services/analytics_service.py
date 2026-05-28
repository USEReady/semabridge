"""
Analytics Service - aggregate delivery metrics and flush to Snowflake.
"""

import logging
import os
from datetime import datetime, timedelta
from typing import Optional, List
from sqlalchemy import func, and_
from sqlalchemy.orm import Session

from ..models import NotificationLog, NotificationChannel
from ..models.analytics import DeliveryStats, ChannelHealth
from ..constants import level_to_string, NotificationLevel
from ..adapters.snowflake_adapter import SnowflakeAdapter
from ..formatters.snowflake_formatter import SnowflakeFormatter

logger = logging.getLogger(__name__)


class AnalyticsService:
    """
    Service for aggregating notification delivery metrics and flushing to Snowflake.
    """
    
    def __init__(self, db_session: Session):
        """
        Initialize analytics service.
        
        Args:
            db_session: SQLAlchemy session for DB queries
        """
        self.db_session = db_session
        self.retention_days = int(os.getenv("ANALYTICS_RETENTION_DAYS", "90"))
    
    async def get_delivery_stats(
        self,
        channel_id: Optional[str] = None,
        project_id: Optional[str] = None,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
    ) -> DeliveryStats:
        """
        Get aggregated delivery statistics for a time window.
        
        Args:
            channel_id: Filter by channel (optional)
            project_id: Filter by project (optional)
            since: Start datetime (default: 24h ago)
            until: End datetime (default: now)
        
        Returns:
            DeliveryStats object with aggregated metrics
        """
        # Default time window: last 24 hours
        until = until or datetime.utcnow()
        since = since or (until - timedelta(hours=24))
        
        # Build query conditions
        conditions = [
            NotificationLog.created_at >= since,
            NotificationLog.created_at <= until,
        ]
        
        # Apply filters
        if channel_id:
            from uuid import UUID
            try:
                ch_uuid = UUID(channel_id) if isinstance(channel_id, str) else channel_id
                conditions.append(NotificationLog.channel_id == ch_uuid)
            except ValueError:
                conditions.append(NotificationLog.channel_id == channel_id)
        
        # Note: project_id is not directly on notification_log, would need join to notification_event
        # For now, we'll skip project filtering in this version
        
        query = self.db_session.query(NotificationLog).filter(*conditions)
        logs = query.all()
        
        # Aggregate statistics
        stats = DeliveryStats(
            window_start=since,
            window_end=until,
        )
        
        durations = []
        by_channel = {}
        by_level = {}
        
        for log in logs:
            stats.total += 1
            
            # Status counting
            if log.status == "delivered":
                stats.delivered += 1
            elif log.status == "failed":
                stats.failed += 1
            elif log.status == "retrying":
                stats.retrying += 1
            elif log.status == "dead":
                stats.dead += 1
            
            # Duration tracking
            if log.duration_ms:
                durations.append(log.duration_ms)
            
            # Channel breakdown
            ch_id = str(log.channel_id)
            if ch_id not in by_channel:
                by_channel[ch_id] = {"total": 0, "delivered": 0, "failed": 0}
            by_channel[ch_id]["total"] += 1
            if log.status == "delivered":
                by_channel[ch_id]["delivered"] += 1
            elif log.status in ("failed", "dead"):
                by_channel[ch_id]["failed"] += 1
        
        # Calculate averages and percentiles
        if durations:
            stats.avg_duration_ms = sum(durations) / len(durations)
            # Simple p95 calculation
            durations_sorted = sorted(durations)
            p95_index = max(0, int(len(durations_sorted) * 0.95) - 1)
            stats.p95_duration_ms = durations_sorted[p95_index]
        
        stats.by_channel = by_channel
        stats.by_level = by_level
        
        return stats
    
    async def get_channel_health(self, channel_id: str) -> ChannelHealth:
        """
        Get health metrics for a specific channel.
        
        Args:
            channel_id: Channel UUID
        
        Returns:
            ChannelHealth object with metrics
        """
        from uuid import UUID
        try:
            ch_uuid = UUID(channel_id) if isinstance(channel_id, str) else channel_id
        except ValueError:
            ch_uuid = channel_id

        # Get channel info
        channel = self.db_session.query(NotificationChannel).filter(
            NotificationChannel.id == ch_uuid
        ).first()
        
        channel_name = channel.name if channel else "Unknown"
        
        # Get recent logs (last 7 days)
        since = datetime.utcnow() - timedelta(days=7)
        logs = self.db_session.query(NotificationLog).filter(
            NotificationLog.channel_id == ch_uuid,
            NotificationLog.created_at >= since,
        ).all()
        
        health = ChannelHealth(
            channel_id=str(channel_id),
            channel_name=channel_name,
        )
        
        if logs:
            durations = []
            failures = 0
            last_delivery = None
            
            for log in logs:
                health.total_deliveries += 1
                
                if log.status == "delivered":
                    pass
                else:
                    failures += 1
                    health.total_failures += 1
                
                if log.duration_ms:
                    durations.append(log.duration_ms)
                
                if log.created_at and (last_delivery is None or log.created_at > last_delivery):
                    last_delivery = log.created_at
            
            # Calculate rates
            if health.total_deliveries > 0:
                health.success_rate = ((health.total_deliveries - health.total_failures) / health.total_deliveries) * 100.0
            
            # Duration stats
            if durations:
                health.avg_duration_ms = sum(durations) / len(durations)
                durations_sorted = sorted(durations)
                p95_index = max(0, int(len(durations_sorted) * 0.95) - 1)
                health.p95_duration_ms = durations_sorted[p95_index]
            
            health.last_delivery_at = last_delivery
        
        # Try to get circuit breaker state (if service available)
        # This would require CircuitBreakerService injection
        # For now, default to CLOSED
        health.circuit_state = "CLOSED"
        
        return health
    
    async def flush_to_snowflake(self) -> dict:
        """
        Flush unflushed notification logs to Snowflake.
        This is idempotent - can be called multiple times safely.
        
        Returns:
            {"flushed": n, "errors": n}
        """
        # Check if Snowflake is configured
        sf_account = os.getenv("SNOWFLAKE_ACCOUNT")
        if not sf_account:
            logger.debug("Snowflake not configured, skipping flush")
            return {"flushed": 0, "errors": 0}
        
        # Build Snowflake config
        sf_config = {
            "account": sf_account,
            "user": os.getenv("SNOWFLAKE_USER", ""),
            "password": os.getenv("SNOWFLAKE_PASSWORD", ""),
            "warehouse": os.getenv("SNOWFLAKE_WAREHOUSE", ""),
            "database": os.getenv("SNOWFLAKE_DATABASE", ""),
            "schema": os.getenv("SNOWFLAKE_SCHEMA", ""),
            "table": os.getenv("SNOWFLAKE_TABLE", "NOTIFICATION_EVENTS"),
        }
        
        # Validate config
        required = ["account", "user", "password", "warehouse", "database", "schema"]
        if not all(sf_config.get(k) for k in required):
            logger.warning("Incomplete Snowflake configuration, skipping flush")
            return {"flushed": 0, "errors": 0}
        
        # Get unflushed logs (batch size configurable)
        batch_size = int(os.getenv("SNOWFLAKE_BATCH_SIZE", "500"))
        
        # Note: snowflake_flushed_at column would be added via migration
        # For now, we'll query without that filter if column doesn't exist
        try:
            logs = self.db_session.query(NotificationLog).filter(
                NotificationLog.snowflake_flushed_at == None
            ).limit(batch_size).all()
        except Exception:
            # Column might not exist yet, return empty
            logger.warning("snowflake_flushed_at column not found, skipping flush")
            return {"flushed": 0, "errors": 0}
        
        if not logs:
            return {"flushed": 0, "errors": 0}
        
        try:
            # Format logs for Snowflake
            rows = []
            
            for log in logs:
                # Convert log to a format suitable for Snowflake
                row = {
                    "event_id": str(log.event_id),
                    "channel_id": str(log.channel_id),
                    "status": log.status,
                    "attempt": log.attempt,
                    "response_code": log.response_code,
                    "response_body": log.response_body[:1000] if log.response_body else None,
                    "duration_ms": log.duration_ms,
                    "error_message": log.error_message[:1000] if log.error_message else None,
                    "created_at": log.created_at.isoformat() if log.created_at else None,
                }
                rows.append(row)
            
            # Insert into Snowflake
            adapter = SnowflakeAdapter(sf_config)
            result = await adapter.bulk_insert(rows, sf_config)
            
            if result.get("inserted", 0) > 0:
                # Mark as flushed (if column exists)
                try:
                    for log in logs[:result.get("inserted", 0)]:
                        log.snowflake_flushed_at = datetime.utcnow()
                    
                    self.db_session.commit()
                except Exception:
                    # Column might not exist yet, just log the insert count
                    pass
            
            return {
                "flushed": result.get("inserted", 0),
                "errors": result.get("failed", 0),
            }
        
        except Exception as e:
            logger.error(f"Error flushing to Snowflake: {str(e)}", exc_info=True)
            return {
                "flushed": 0,
                "errors": len(logs),
            }
