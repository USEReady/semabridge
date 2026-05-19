# SemaBridge Notifications System

A production-grade, enterprise-ready centralized notification pipeline for SemaBridge.

## Overview

The notifications system provides async-first, fault-tolerant delivery of notifications across multiple channels:

- **Slack** - via incoming webhooks with rich Block Kit formatting
- **Email** - via SMTP with async delivery and HTML templates
- **Generic Webhooks** - for custom integrations
- **Teams** - ✅ Adaptive Cards via workflow webhooks
- **PagerDuty** - ✅ Events API v2 integration (trigger/resolve)

### Key Features

✅ **Non-blocking** - Sync engine never awaits notification delivery  
✅ **Reliable** - Redis Streams with ACK semantics and crash recovery  
✅ **Deduplicating** - Fingerprint-based duplicate detection within TTL windows  
✅ **Batching** - Aggregates WARNING/ERROR events to prevent storms  
✅ **Quiet Hours** - Timezone-aware suppression with DST handling (Phase 2)  
✅ **Digest** - Batch and flush suppressed events at quiet hours end (Phase 2)  
✅ **Circuit Breaker** - Auto-disables channels after repeated failures (Phase 2)  
✅ **Auto-Alerting** - Notifies ops when channels fail (Phase 2)  
✅ **SSRF Protected** - Validates all webhook URLs against private IP ranges  
✅ **Secret Handling** - Encrypts at rest, masks in API responses  
✅ **Observable** - Structured logging and delivery audit trail  
✅ **Resilient** - Exponential backoff retry with dead-letter handling  

## Architecture

### Components

```
┌─────────────────────────────────────────────────────────────┐
│                     Sync Engine                             │
│            notification_service.emit(event)                 │
└────────────────────────┬────────────────────────────────────┘
                         │ (async, non-blocking)
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                    Redis Streams                            │
│  Queue: semabridge:notifications (main delivery queue)      │
│  Queue: semabridge:retry (failed, awaiting retry)           │
│  Queue: semabridge:dead-letters (max retries exceeded)      │
│  Queue: semabridge:digest-staging (quiet hours suppressed)  │
└──────┬──────────┬──────────────┬────────────────────────────┘
       │          │              │
       ▼          ▼              ▼
┌──────────────┬──────────┬──────────────┐
│ Dispatcher   │ Retry    │ Dead-Letter  │
│ Worker       │ Worker   │ Worker       │
└──────┬───────┴────┬─────┴──────────────┘
       │            │
       ▼            ▼
┌─────────────────────────────────────┐
│   Deduplication / Batching          │
│   Routing / Formatting              │
└──────────┬──────────────────────────┘
           │
       ┌───┴──────┬────────────┬──────────┐
       ▼          ▼            ▼          ▼
    Slack      Email       Webhook    Teams/PagerDuty
   Adapter    Adapter      Adapter    (Phase 2)
       │          │            │          │
       └──────────┼────────────┴──────────┘
                  │
                  ▼
         External Services
    (Slack, SMTP, Custom APIs)
```

### Data Flow

1. **Event Emission** - Sync engine creates `NotificationEvent` and calls `emit(event)`
   - Method returns immediately (non-blocking)
   - Event serialized and enqueued to Redis

2. **Dispatcher** - Consumes from main queue
   - Routes to matching channels (by level, project scope, status)
   - Applies deduplication (fingerprint check)
   - Applies batching (if enabled and level permits)
   - Formats event for channel type
   - Sends via adapter
   - Logs result
   - On failure: re-queues to retry queue

3. **Retry Logic** - Consumes from retry queue  
   - Checks if ready (exponential backoff: 5s → 30s → 120s)
   - If max retries exceeded: moves to dead-letter
   - Otherwise: re-queues to main dispatcher

4. **Dead-Letter** - Consumes from dead-letter queue
   - Logs permanently failed messages
   - (Phase 2) Disables channel if threshold exceeded
   - (Phase 2) Alerts operators

### Database Schema

#### notification_channels
Stores channel configurations with encryption at rest.

```sql
CREATE TABLE notification_channels (
  id UUID PRIMARY KEY,
  name VARCHAR(255) NOT NULL,
  channel_type ENUM NOT NULL,
  enabled BOOLEAN DEFAULT TRUE,
  config_json TEXT NOT NULL,  -- Encrypted
  level_mask INTEGER DEFAULT 63,
  project_scope VARCHAR(255),
  quiet_hours_enabled BOOLEAN DEFAULT FALSE,
  quiet_hours_start TIME,
  quiet_hours_end TIME,
  timezone VARCHAR(63) DEFAULT 'UTC',
  digest_enabled BOOLEAN DEFAULT FALSE,
  status ENUM NOT NULL DEFAULT 'ACTIVE',
  created_at DATETIME DEFAULT NOW(),
  updated_at DATETIME DEFAULT NOW()
);
```

#### notification_logs
Audit trail of delivery attempts.

```sql
CREATE TABLE notification_logs (
  id UUID PRIMARY KEY,
  event_id UUID NOT NULL,
  channel_id UUID NOT NULL REFERENCES notification_channels(id),
  status ENUM NOT NULL,
  attempt INTEGER DEFAULT 1,
  response_code INTEGER,
  response_body TEXT,
  duration_ms INTEGER,
  error_message TEXT,
  created_at DATETIME DEFAULT NOW()
);
```

#### notification_dedupes
Fingerprints with TTL for deduplication.

```sql
CREATE TABLE notification_dedupes (
  fingerprint VARCHAR(64) PRIMARY KEY,
  expires_at DATETIME NOT NULL,
  created_at DATETIME DEFAULT NOW()
);
```

### Domain Model

All internal logic uses the canonical `NotificationEvent` model:

```python
@dataclass
class NotificationEvent:
    id: UUID
    correlation_id: str
    sync_job_id: Optional[str]
    project_id: Optional[str]
    type: str
    level: int  # Bitmask
    title: str
    message: str
    payload: dict
    source: str
    created_at: datetime
    sequence_number: int
    fingerprint: str
```

**Level bitmask values:**
- `SYNC_RESULT` = 1
- `CRITICAL` = 2
- `ERROR` = 4
- `WARNING` = 8
- `INFO` = 16
- `DEBUG` = 32
- `ALL` = 63

## Configuration

### Environment Variables

```bash
# Redis
REDIS_URL=redis://localhost:6379

# Deduplication
NOTIFICATION_DEDUPE_TTL_SEC=300              # Default: 5 minutes

# Batching
NOTIFICATION_BATCH_WINDOW_SEC=30             # Default: 30 seconds

# Retries
NOTIFICATION_MAX_RETRIES=3                   # Default: 3 attempts
NOTIFICATION_CIRCUIT_BREAKER_THRESHOLD=3     # Default: 3 consecutive failures
NOTIFICATION_CIRCUIT_BREAKER_COOLDOWN_SEC=60 # Default: 60 seconds

# SMTP (for Email adapter)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USERNAME=notifications@example.com
SMTP_PASSWORD=secret
SMTP_FROM_ADDRESS=semabridge@example.com
SMTP_USE_TLS=true

# Encryption (for secrets at rest)
NOTIFICATION_SECRET_KEY=... # AES-256 or Fernet key for encrypting config_json
```

## API Endpoints

All endpoints use `/api/settings/` prefix.

### Channels

**List channels:**
```
GET /notification-channels?skip=0&limit=10&enabled=true
Response: { items: [...], total: 50, page: 0, page_size: 10, total_pages: 5 }
```

**Create channel:**
```
POST /notification-channels
Body: {
  "name": "Production Alerts",
  "channel_type": "slack",
  "config_json": { "webhook_url": "https://hooks.slack.com/..." },
  "level_mask": 14,  // ERROR | WARNING | CRITICAL
  "timezone": "America/New_York"
}
Response: 201 Created { id, name, channel_type, ... (secrets masked) }
```

**Delete channel:**
```
DELETE /notification-channels/{channel_id}
Response: 204 No Content
```

**Test channel:**
```
POST /notification-channels/{channel_id}/test
Response: { "success": true, "message": "..." }
```

### Delivery Logs

**List logs:**
```
GET /notification-log?skip=0&limit=50&channel_id=...&status=failed
Response: { items: [...], total: 1000, ... }
```

**Retry delivery:**
```
POST /notification-log/retry
Body: { "log_id": "..." }
Response: { "success": true, "message": "..." }
```

## Usage Example

### From Sync Engine

```python
from semabridge.notifications import NotificationService, NotificationEvent, NotificationLevel

# Initialize (typically done once at startup)
notification_service = NotificationService(redis_url="redis://localhost:6379")

# Create and emit event
event = NotificationEvent(
    correlation_id="sync_12345",
    sync_job_id="job_abc123",
    project_id="proj_xyz",
    type="sync_completion",
    level=NotificationLevel.ERROR | NotificationLevel.WARNING,
    title="Sync Job Failed",
    message="Data sync encountered 5 errors and 12 warnings",
    source="sync_engine",
)

# Emit (returns immediately, non-blocking)
success = await notification_service.emit(event)
if not success:
    logger.warning("Notification queue unavailable, but sync continues")
```

## Channel Configuration Examples

### Slack

```json
{
  "webhook_url": "https://hooks.slack.com/services/T123/B456/abcdef..."
}
```

Formats as Block Kit with severity colors:
- CRITICAL: 🔴 Red
- ERROR: 🟠 Orange  
- WARNING: 🟡 Yellow
- INFO: 🟢 Green

### Email

```json
{
  "host": "smtp.gmail.com",
  "port": 587,
  "username": "alerts@example.com",
  "password": "app_specific_password",
  "from_address": "alerts@example.com",
  "to_addresses": ["devops@example.com", "oncall@example.com"],
  "use_tls": true
}
```

Sends HTML email with plaintext fallback and sanitized HTML.

### Generic Webhook

```json
{
  "webhook_url": "https://your-api.example.com/webhooks/notifications"
}
```

POSTs JSON payload with SSRF protection (blocks private IPs).

## Deployment

### Prerequisites

- Redis 6.2+ (for Streams support)
- Python 3.9+
- Database with migrations applied
- Environment variables configured

### Running Workers

```python
import asyncio
from semabridge.notifications.workers.dispatcher import DispatcherWorker
from semabridge.notifications.workers.retry_worker import RetryWorker
from semabridge.notifications.workers.dead_letter_worker import DeadLetterWorker

async def main():
    # Start workers (typically in separate processes/containers)
    dispatcher = DispatcherWorker(redis_url, db_session, max_workers=5)
    retry = RetryWorker(redis_url, db_session)
    dead_letter = DeadLetterWorker(redis_url)
    
    await asyncio.gather(
        dispatcher.start(),
        retry.start(),
        dead_letter.start(),
    )

asyncio.run(main())
```

### Docker Compose Example

```yaml
version: '3.8'
services:
  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  dispatcher:
    build: .
    command: python -m semabridge.notifications.workers.dispatcher
    environment:
      REDIS_URL: redis://redis:6379
    depends_on:
      - redis

  retry:
    build: .
    command: python -m semabridge.notifications.workers.retry_worker
    environment:
      REDIS_URL: redis://redis:6379
    depends_on:
      - redis
```

## Testing

### Unit Tests

```bash
pytest backend/tests/notifications/ -v --cov
```

Test coverage targets: **80%+ coverage**

Test files:
- `test_notification_event.py` - Model and fingerprint
- `test_dedupe_service.py` - Deduplication logic
- `test_batching_service.py` - Event aggregation
- `test_slack_adapter.py` - Slack delivery
- `test_email_adapter.py` - Email delivery, sanitization
- `test_webhook_adapter.py` - Webhook delivery, SSRF protection
- `test_validators.py` - URL and config validation
- `test_redis_streams.py` - Queue operations
- `test_api_routes.py` - REST endpoints, secret masking

### Integration Tests

```bash
# Requires Redis and database
pytest backend/tests/notifications/ -m integration -v
```

## Observability

### Structured Logging

All delivery attempts emit structured logs:

```json
{
  "event_id": "550e8400-e29b-41d4-a716-446655440000",
  "correlation_id": "sync_12345",
  "channel": "slack-prod",
  "channel_id": "ch_abc123",
  "attempt": 1,
  "duration_ms": 245,
  "provider_response": "ok",
  "status": "delivered"
}
```

### Metrics (Phase 2)

- Delivery success rate per channel
- Average delivery latency  
- Queue depth and processing rate
- Channel circuit breaker state transitions

## Security Considerations

### SSRF Protection

All webhook URLs are validated against:
- Private IP ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)
- Loopback addresses (127.0.0.1, ::1)
- Link-local (169.254.0.0/16)
- AWS metadata (169.254.169.254)
- Reserved hostnames (.local, .internal, .localhost)

Validation occurs at:
1. Channel creation/update (API layer)
2. Delivery time (adapter layer) — hostname re-resolved to prevent TOCTOU

### Secret Storage

- Channel configuration stored encrypted at rest using AES-256 or Fernet
- Webhook URLs, API keys, passwords encrypted in `config_json` column
- API responses always mask secrets (last 4 chars visible for URLs/keys)
- Secrets never logged

### HTML Sanitization

Email payloads sanitized with `bleach`:
- Allowed tags: p, br, div, span, h1-h3, strong, b, i, em, ul, ol, li, table, tr, td, th
- All attributes stripped except safe ones
- Script tags and events completely removed

## Known Limitations & TODOs

### Phase 1
- [x] Core infrastructure (models, queue, services)
- [x] Slack, Email, Webhook adapters
- [x] Deduplication and batching
- [x] Retry and dead-letter handling
- [x] SSRF protection
- [x] Secret masking
- [x] Database migrations (use Alembic)
- [x] Comprehensive test suite
- [x] Circuit breaker (framework implemented, enabled by default)

### Phase 2
- [x] Teams Adaptive Cards adapter
- [x] PagerDuty Events API v2 adapter (trigger/resolve)
- [x] Quiet hours service (timezone-aware with DST handling)
- [x] Digest worker (batch and flush at quiet hours end)
- [x] Circuit breaker with channel auto-disable
- [x] Dead-letter persistence and auto-alerting
- [x] Comprehensive Phase 2 test suite (Teams, PagerDuty, Quiet Hours, Circuit Breaker, Digest, Dead-Letter)
- [ ] Metrics and dashboarding
- [ ] Admin UI for channel management
- [ ] Secret encryption at rest (framework in place, needs key management)

### Phase 3
- [x] Snowflake notification adapter (persistent audit sink)
- [x] Analytics service (delivery statistics, Snowflake flush)
- [x] Replay service (re-process historical events)
- [x] Notification templates (Jinja2 with sandbox)
- [x] Advanced routing (rule-based engine)
- [ ] UI settings pages (channels, rules, templates, logs, analytics)

## Phase 2 Features

### Teams Adapter

Uses Adaptive Cards via Teams Workflow webhooks.

**Configuration:**
```json
{
  "webhook_url": "https://outlook.webhook.office.com/webhookb2/..."
}
```

**Features:**
- Rich Adaptive Card formatting with severity colors
- Plaintext fallback if card validation fails
- Rate limit handling with Retry-After exponential backoff
- SSRF protection on webhook URLs

**Card Structure:**
- Title with severity color (Red, Orange, Yellow, Green)
- Message body (truncated at 2000 chars)
- Fact set with Job ID, Project, Correlation ID, Timestamp
- Plaintext fallback for legacy Teams or validation failures

### PagerDuty Adapter

Uses PagerDuty Events API v2 for incident management.

**Configuration:**
```json
{
  "routing_key": "1234567890abcdef1234567890abcdef"
}
```

**Features:**
- Level → action mapping (CRITICAL/ERROR → trigger)
- Deduplication via `dedup_key` (prevents duplicate incidents)
- Automatic incident resolution on success events
- Custom details include job ID, project, correlation ID

**Level Mapping:**
| Level | Action | Severity |
|-------|--------|----------|
| CRITICAL | trigger | critical |
| ERROR | trigger | error |
| WARNING | trigger | warning |
| INFO | trigger | info |
| SYNC_RESULT + "success" | resolve | - |

### Quiet Hours Service

Timezone-aware notification suppression with DST handling.

**Features:**
- Uses `zoneinfo` (PEP 615) for all timezone operations — never manual UTC math
- Supports same-day windows (09:00-17:00) and overnight windows (22:00-07:00)
- Handles DST spring-forward and fall-back transitions correctly
- CRITICAL level bypasses quiet hours by default
- Configurable per-channel bypass levels

**Configuration:**
```json
{
  "quiet_hours_enabled": true,
  "quiet_hours_start": "22:00",
  "quiet_hours_end": "07:00",
  "timezone": "America/New_York",
  "bypass_quiet_hours_levels": 2,  // CRITICAL = 2 (bitmask)
  "digest_enabled": true
}
```

**Methods:**
- `is_quiet(channel)` - Returns True if current time is in quiet window
- `seconds_until_window_end(channel)` - Used by digest worker for scheduling
- `should_bypass(event, channel)` - Checks if event should ignore quiet hours
- `validate_timezone(tz_name)` - Validates IANA timezone names

### Digest Worker

Batches and flushes suppressed events when quiet hours end.

**Flow:**
1. Polls every 60 seconds (configurable)
2. For each channel with `digest_enabled=True`:
   - Checks if quiet hours have ended
   - Pulls all staged events from `semabridge:digest-staging:{channel_id}`
   - Groups by level and counts (e.g., "5 errors, 3 warnings")
   - Formats a synthetic digest event
   - Sends via the channel's adapter
   - Clears the staging queue

**Digest Message Format:**
```
Digest — quiet hours 22:00-07:00 (America/New_York)

2 sync results
1 critical
5 errors
3 warnings

Oldest: 2024-01-15T06:30:00Z
Newest: 2024-01-15T06:55:00Z
```

### Circuit Breaker

Automatically disables channels after repeated failures, enabling graceful degradation.

**State Machine:**
- **CLOSED** - Normal operation
- **OPEN** - Stop calling service, wait for cooldown
- **HALF_OPEN** - After cooldown expires, allow one probe request

**Thresholds:**
- Opens after 3 consecutive failures (configurable)
- Cooldown 60 seconds (configurable) before half-open probe
- Failure counter auto-resets after 5 minutes of inactivity

**Storage (Redis):**
- `semabridge:cb:{channel_id}:failures` - Failure count (TTL: 5 min)
- `semabridge:cb:{channel_id}:cooldown_until` - Cooldown expiry timestamp
- `semabridge:cb:{channel_id}:half_open` - Probe flag during recovery

**Integration:**
- Dispatcher checks circuit before sending
- Records success/failure after each attempt
- Auto-updates channel status in DB when opening/closing

### Dead-Letter Alerting

Auto-disables failing channels and alerts operations.

**Flow:**
1. DeadLetterWorker consumes from dead-letter queue
2. Increments per-channel dead-letter count (1-hour TTL)
3. If count >= threshold (default: 5):
   - Disables the channel (`status = DISABLED`)
   - Logs structured alert with channel ID and count
   - Emits alert event to other healthy channels
4. Operators receive notification on Slack/Email/Teams

**Structured Alert Log:**
```json
{
  "event": "channel_auto_disabled",
  "channel_id": "...",
  "channel_name": "slack-prod",
  "dead_letter_count": 5,
  "timestamp": "2024-01-15T07:30:00Z"
}
```

## Environment Variables (Phase 2)

```bash
# Quiet Hours & Digest
DIGEST_POLL_INTERVAL_SEC=60                  # Default: 60

# Circuit Breaker
CIRCUIT_BREAKER_THRESHOLD=3                  # Default: 3 consecutive failures
CIRCUIT_BREAKER_COOLDOWN_SEC=60              # Default: 60 seconds

# Dead-Letter Alerting
DEAD_LETTER_ALERT_THRESHOLD=5                # Default: 5 consecutive dead-letters
```

## Phase 3 Features

### Snowflake Adapter

Persists notification events to a Snowflake table for audit trails and analytics.
This is a **write-only sink**, not a delivery channel for user notifications.

**Configuration:**
```json
{
  "account": "xy12345.us-east-1",
  "user": "notification_user",
  "password": "encrypted_password",
  "warehouse": "COMPUTE_WH",
  "database": "ANALYTICS",
  "schema": "NOTIFICATIONS",
  "table": "NOTIFICATION_EVENTS"
}
```

**Features:**
- Async insert via executor (connector is sync-only)
- Connection pooling by (account, database, schema)
- Reconnect on OperationalError with stale connection removal
- Bulk insert for batch operations
- Integration with AnalyticsService and ReplayService

**Table Schema:**
```sql
CREATE TABLE NOTIFICATION_EVENTS (
  event_id VARCHAR,
  correlation_id VARCHAR,
  sync_job_id VARCHAR,
  project_id VARCHAR,
  level VARCHAR,
  level_numeric INTEGER,
  title VARCHAR,
  message VARCHAR,
  source VARCHAR,
  type VARCHAR,
  created_at TIMESTAMP,
  payload_json VARIANT,
  sequence_number INTEGER
);
```

### Analytics Service

Aggregates delivery metrics and flushes event data to Snowflake (if configured).

**Methods:**
- `get_delivery_stats(channel_id, project_id, since, until)` — Returns DeliveryStats with total/delivered/failed/retrying/dead counts, per-channel and per-level breakdowns, average and p95 latency
- `get_channel_health(channel_id)` — Returns ChannelHealth with success rate, latency percentiles, circuit breaker state, last delivery timestamp
- `flush_to_snowflake()` — Pulls unflushed rows, batches, calls bulk_insert, marks as flushed (idempotent)

**Metrics returned:**
- Total, delivered, failed, retrying, dead
- Average duration_ms, p95 duration_ms
- Per-channel breakdown (counts)
- Per-level breakdown (counts)
- Time window (start/end)

**Configuration:**
```
ANALYTICS_RETENTION_DAYS=90           # Default: 90 (logs older excluded from stats)
```

### Replay Service

Allows operators to replay failed or dead-letter notifications without re-running sync jobs.

**Methods:**
- `replay_event(log_id, target_channel_ids)` — Re-enqueues original event to specified channels (or original channels if not specified), bypasses deduplication, respects circuit breaker and quiet hours
- `replay_bulk(filter)` — Replays matching logs (status, channel, level, date range), batches of 50, enforces 500 max limit
- `get_replay_candidates(filter)` — Preview which logs would match without replaying

**Replay features:**
- ✅ Bypasses deduplication (intentional re-delivery)
- ✅ Respects circuit breaker (open channels skipped)
- ✅ Respects quiet hours (suppressed events staged for digest)
- ✅ Adds `replayed_from_log_id` to event payload for traceability
- ✅ Creates new log row with source="replay"

**Configuration:**
```
REPLAY_MAX_BULK_LIMIT=500             # Default: 500 max rows per bulk replay
```

### Notification Templates

Per-channel message templates with Jinja2 variable substitution and sandboxed rendering.

**Features:**
- Jinja2 sandbox environment (blocks `__class__`, `__globals__`, `__builtins__`)
- Per-channel template per level (multi-level support)
- Fallback to is_default template
- Live preview API for testing
- Template validation with dry-run rendering
- Safe variable access: `{{ title }}`, `{{ level_str }}`, `{{ payload.key }}`

**Template variables available:**
- `{{ title }}`, `{{ message }}`
- `{{ level }}` (numeric), `{{ level_str }}` (name)
- `{{ project_id }}`, `{{ sync_job_id }}`, `{{ correlation_id }}`
- `{{ source }}`, `{{ created_at }}`
- `{{ payload.<key> }}` (nested access)

**Configuration:**
```
TEMPLATE_MAX_LENGTH_CHARS=4000        # Default: 4000 per template
TEMPLATE_MAX_RENDERED_CHARS=8000      # Default: 8000 max rendered output
```

### Advanced Routing

Rule-based routing engine with conditions, priorities, and fallback chains.

**Routing rule conditions (all optional, ANDed together):**
- `level_mask` - Bitmask of levels
- `project_ids` - List of project IDs
- `source_pattern` - fnmatch wildcard pattern (e.g., `sync_*`)
- `title_contains` - Case-insensitive substring
- `payload_key_exists` - Event has this key in payload
- `payload_value_matches` - Nested key-value match

**Features:**
- Evaluated in priority order (lower = higher priority)
- `stop_on_match=True` halts chain after match
- Results deduplicated (channel not notified twice)
- Fallback to legacy level-mask + project-scope routing if no rules match
- Pure Python evaluation (no eval/exec, fnmatch for patterns)

**Configuration:**
```
ROUTING_RULES_CACHE_TTL_SEC=60        # Default: 60s (Redis cache of DB rules)
```

**Example routing rule:**
```json
{
  "priority": 1,
  "name": "Production errors to PagerDuty",
  "conditions": {
    "level_mask": 6,  // ERROR | CRITICAL
    "project_ids": ["prod-web", "prod-api"],
    "source_pattern": "sync_*"
  },
  "channel_ids": ["pagerduty_channel_id"],
  "stop_on_match": true,
  "enabled": true
}
```

## Phase 3 Environment Variables

```bash
# Snowflake
SNOWFLAKE_ACCOUNT                      # e.g., xy12345.us-east-1
SNOWFLAKE_USER                         # Snowflake user
SNOWFLAKE_PASSWORD                     # Snowflake password (encrypted)
SNOWFLAKE_WAREHOUSE                    # e.g., COMPUTE_WH
SNOWFLAKE_DATABASE                     # e.g., ANALYTICS
SNOWFLAKE_SCHEMA                       # e.g., NOTIFICATIONS
SNOWFLAKE_TABLE                        # Default: NOTIFICATION_EVENTS
SNOWFLAKE_BATCH_SIZE                   # Default: 500
SNOWFLAKE_FLUSH_INTERVAL_SEC           # Default: 300

# Analytics
ANALYTICS_RETENTION_DAYS               # Default: 90

# Replay
REPLAY_MAX_BULK_LIMIT                  # Default: 500

# Templates
TEMPLATE_MAX_LENGTH_CHARS              # Default: 4000
TEMPLATE_MAX_RENDERED_CHARS            # Default: 8000

# Routing
ROUTING_RULES_CACHE_TTL_SEC            # Default: 60
```

## Troubleshooting

### Events not being delivered

1. Check Redis connectivity: `redis-cli ping`
2. Check queue depth: `redis-cli XLEN semabridge:notifications`
3. Check dispatcher logs: `docker logs <dispatcher-container>`
4. Verify channel configuration: `SELECT * FROM notification_channels WHERE enabled = true`

### High latency

1. Check Redis latency: `redis-cli --latency`
2. Increase dispatcher worker count
3. Check adapter timeout settings
4. Monitor external service (Slack, SMTP) response times

### Memory growth

1. Check Redis memory: `redis-cli INFO memory`
2. Run stream cleanup: `XTRIM semabridge:notifications MAXLEN 10000 APPROXIMATE`
3. Check for stuck workers consuming without ACKing

## Contributing

When adding new channels:

1. Create adapter inheriting from `BaseAdapter`
2. Create formatter inheriting from `BaseFormatter`
3. Register in `DispatcherWorker.ADAPTERS` and `FORMATTERS` dicts
4. Add comprehensive tests
5. Update this README

## License

Same as SemaBridge

## Support

For issues and questions, please refer to the SemaBridge main documentation.
