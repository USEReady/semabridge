"""
Tests for Template Service.
"""

import pytest
from datetime import datetime
from uuid import uuid4

from semabridge.notifications.services.template_service import (
    TemplateService,
    NotificationTemplate,
    TemplateRenderError,
)
from semabridge.notifications.models import NotificationEvent


@pytest.fixture
def template_service():
    """Template service fixture."""
    return TemplateService()


@pytest.fixture
def sample_event():
    """Sample notification event."""
    return NotificationEvent(
        id=uuid4(),
        correlation_id="corr_123",
        sync_job_id="job_456",
        project_id="proj_789",
        type="sync_completion",
        level=4,  # ERROR
        title="Sync Failed",
        message="Data sync encountered errors",
        payload={"error_count": 5, "duration_sec": 120},
        source="sync_engine",
        created_at=datetime.utcnow(),
    )


def test_template_render_basic_variables(template_service, sample_event):
    """Test rendering basic template variables."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="ERROR: {{ title }}",
        body_template="{{ level_str }} - {{ message }}",
    )
    
    title, body = template_service.render(template, sample_event)
    
    assert "ERROR: Sync Failed" in title
    assert "ERROR - Data sync encountered errors" in body


def test_template_render_all_variables(template_service, sample_event):
    """Test all documented template variables."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ title }}",
        body_template=(
            "Level: {{ level }} ({{ level_str }})\n"
            "Project: {{ project_id }}\n"
            "Job: {{ sync_job_id }}\n"
            "Correlation: {{ correlation_id }}\n"
            "Source: {{ source }}\n"
            "Time: {{ created_at }}"
        ),
    )
    
    title, body = template_service.render(template, sample_event)
    
    assert "Sync Failed" in title
    assert "ERROR" in body
    assert "proj_789" in body
    assert "job_456" in body
    assert "corr_123" in body
    assert "sync_engine" in body


def test_template_render_payload_access(template_service, sample_event):
    """Test accessing payload fields."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ title }}",
        body_template=(
            "Errors: {{ payload.error_count }}\n"
            "Duration: {{ payload.duration_sec }}s"
        ),
    )
    
    title, body = template_service.render(template, sample_event)
    
    assert "Errors: 5" in body
    assert "Duration: 120s" in body


def test_template_render_payload_nested_access(template_service):
    """Test nested payload access."""
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr",
        title="Test",
        message="msg",
        level=1,
        payload={"db": {"host": "localhost", "port": 5432}},
    )
    
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ title }}",
        body_template="DB: {{ payload.db.host }}:{{ payload.db.port }}",
    )
    
    title, body = template_service.render(template, event)
    
    assert "localhost:5432" in body


def test_template_sandbox_rejects_class(template_service, sample_event):
    """Test sandbox rejects __class__ access."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ ''.__class__ }}",
        body_template="test",
    )
    
    with pytest.raises(TemplateRenderError):
        template_service.render(template, sample_event)


def test_template_sandbox_rejects_globals(template_service, sample_event):
    """Test sandbox rejects __globals__ access."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ test.__globals__ }}",
        body_template="test",
    )
    
    with pytest.raises(TemplateRenderError):
        template_service.render(template, sample_event)


def test_template_sandbox_rejects_builtins(template_service, sample_event):
    """Test sandbox rejects __builtins__ access."""
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ __builtins__ }}",
        body_template="test",
    )
    
    with pytest.raises(TemplateRenderError):
        template_service.render(template, sample_event)


def test_template_max_length_title(template_service, sample_event):
    """Test title template max length enforcement."""
    long_template = "X" * 5000  # Exceeds 4000 default
    
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template=long_template,
        body_template="test",
    )
    
    title, body = template_service.render(template, sample_event)
    
    # Should be truncated
    assert len(title) <= template_service.MAX_RENDERED_LENGTH


def test_template_max_length_body(template_service, sample_event):
    """Test body template max length enforcement."""
    long_template = "Y" * 9000  # Exceeds 8000 default
    
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="test",
        body_template=long_template,
    )
    
    title, body = template_service.render(template, sample_event)
    
    # Should be truncated
    assert len(body) <= template_service.MAX_RENDERED_LENGTH


@pytest.mark.asyncio
async def test_validate_template_syntax_error(template_service):
    """Test validate_template catches syntax errors."""
    errors = await template_service.validate_template(
        "{{ unclosed",
        "test",
    )
    
    assert len(errors) > 0
    assert any("syntax" in e.lower() for e in errors)


@pytest.mark.asyncio
async def test_validate_template_valid(template_service):
    """Test validate_template accepts valid templates."""
    errors = await template_service.validate_template(
        "Title: {{ title }}",
        "Message: {{ message }}",
    )
    
    assert len(errors) == 0


@pytest.mark.asyncio
async def test_validate_template_too_long(template_service):
    """Test validate_template rejects overly long templates."""
    long_template = "X" * 5000
    
    errors = await template_service.validate_template(
        long_template,
        "body",
    )
    
    assert len(errors) > 0
    assert any("length" in e.lower() for e in errors)


def test_template_render_conditional(template_service):
    """Test template with Jinja2 conditional."""
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr",
        title="Test",
        message="msg",
        level=4,  # ERROR
    )
    
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{% if level_str == 'ERROR' %}URGENT: {% endif %}{{ title }}",
        body_template="test",
    )
    
    title, body = template_service.render(template, event)
    
    assert "URGENT:" in title


def test_template_render_loop(template_service):
    """Test template with Jinja2 loop (if payload is list)."""
    event = NotificationEvent(
        id=uuid4(),
        correlation_id="corr",
        title="Test",
        message="msg",
        level=1,
        payload={"errors": ["Error 1", "Error 2", "Error 3"]},
    )
    
    template = NotificationTemplate(
        id="test",
        channel_id="ch_123",
        level_mask=63,
        title_template="{{ title }}",
        body_template="{% for err in payload.errors %}- {{ err }}\n{% endfor %}",
    )
    
    title, body = template_service.render(template, event)
    
    assert "Error 1" in body
    assert "Error 2" in body
    assert "Error 3" in body


def test_context_building(template_service, sample_event):
    """Test context building from event."""
    context = template_service._build_context(sample_event)
    
    assert context["title"] == sample_event.title
    assert context["message"] == sample_event.message
    assert context["level"] == sample_event.level
    assert context["level_str"] == "ERROR"
    assert context["project_id"] == sample_event.project_id
    assert context["sync_job_id"] == sample_event.sync_job_id
    assert context["correlation_id"] == sample_event.correlation_id
    assert context["source"] == sample_event.source
    assert context["payload"] == sample_event.payload
