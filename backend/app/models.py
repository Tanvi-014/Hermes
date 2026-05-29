import enum
from datetime import datetime, timezone
import uuid
from sqlalchemy import Column, String, Integer, DateTime, ForeignKey, Text, Index, Boolean
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class WebhookStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"  # Dead Letter Queue

class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, default="anonymous")
    event_id = Column(String, nullable=False, default=lambda: str(uuid.uuid4()))
    destination_url = Column(String, nullable=False)
    payload = Column(JSONB, nullable=False)
    headers = Column(JSONB, nullable=False)
    idempotency_key = Column(String, nullable=True)
    status = Column(String, nullable=False, default=WebhookStatus.PENDING.value)
    retry_count = Column(Integer, nullable=False, default=0)
    max_retries = Column(Integer, nullable=False, default=5)
    
    next_attempt_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    attempts = relationship("DeliveryAttempt", back_populates="webhook", cascade="all, delete-orphan", lazy="selectin")

    def to_dict(self):
        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "event_id": self.event_id,
            "destination_url": self.destination_url,
            "payload": self.payload,
            "headers": self.headers,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "retry_count": self.retry_count,
            "max_retries": self.max_retries,
            "next_attempt_at": self.next_attempt_at.isoformat() if self.next_attempt_at else None,
            "last_attempt_at": self.last_attempt_at.isoformat() if self.last_attempt_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        Index("ix_webhooks_status_next_attempt_at", "status", "next_attempt_at"),
        Index("ix_webhooks_created_at", "created_at"),
        Index("ix_webhooks_tenant_created_at", "tenant_id", "created_at"),
        Index("ix_webhooks_event_id", "event_id"),
        Index("ix_webhooks_tenant_destination_idempotency_key", "tenant_id", "destination_url", "idempotency_key", unique=True),
    )

class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    status_code = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    attempted_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    webhook = relationship("Webhook", back_populates="attempts")

    def to_dict(self):
        return {
            "id": str(self.id),
            "webhook_id": str(self.webhook_id),
            "attempt_number": self.attempt_number,
            "status_code": self.status_code,
            "response_body": self.response_body,
            "duration_ms": self.duration_ms,
            "error_message": self.error_message,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
        }

    __table_args__ = (
        Index("ix_delivery_attempts_webhook_id_attempt_number", "webhook_id", "attempt_number"),
    )


class AlertChannelType(str, enum.Enum):
    SLACK = "slack"
    EMAIL = "email"


class AlertConfig(Base):
    """
    Stores alert destinations per tenant. When a webhook enters the DLQ,
    Hermes fires notifications to every enabled AlertConfig for that tenant.
    """
    __tablename__ = "alert_configs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id = Column(String, nullable=False, default="anonymous")
    name = Column(String, nullable=False)  # Human-readable label, e.g. "#ops-alerts"
    channel_type = Column(String, nullable=False)  # "slack" or "email"
    config = Column(JSONB, nullable=False)
    # Slack config:  {"webhook_url": "https://hooks.slack.com/services/..."}
    # Email config:  {"smtp_host": "...", "smtp_port": 587, "username": "...", "password": "...", "from": "...", "to": "dev@company.com"}
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))

    def to_dict(self):
        # Redact sensitive fields in config before sending to frontend
        safe_config = {**self.config} if self.config else {}
        for sensitive_key in ("password", "smtp_password"):
            if sensitive_key in safe_config:
                safe_config[sensitive_key] = "••••••••"
        # Show only last 20 chars of webhook URLs
        if "webhook_url" in safe_config and len(safe_config["webhook_url"]) > 20:
            safe_config["webhook_url"] = "…" + safe_config["webhook_url"][-20:]

        return {
            "id": str(self.id),
            "tenant_id": self.tenant_id,
            "name": self.name,
            "channel_type": self.channel_type,
            "config": safe_config,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    __table_args__ = (
        Index("ix_alert_configs_tenant_id", "tenant_id"),
    )


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    memberships = relationship("ProjectMember", back_populates="user", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "email": self.email,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, nullable=False)
    api_key = Column(String, unique=True, nullable=False, default=lambda: f"hk_live_{uuid.uuid4().hex}")
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    members = relationship("ProjectMember", back_populates="project", cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": str(self.id),
            "name": self.name,
            "api_key": self.api_key,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }


class ProjectMember(Base):
    __tablename__ = "project_members"

    project_id = Column(UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), primary_key=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role = Column(String, nullable=False, default="viewer")  # "owner", "admin", "viewer"
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    user = relationship("User", back_populates="memberships")
    project = relationship("Project", back_populates="members")

    def to_dict(self):
        return {
            "project_id": str(self.project_id),
            "user_id": str(self.user_id),
            "role": self.role,
            "email": self.user.email if self.user else None,
            "created_at": self.created_at.isoformat() if self.created_at else None
        }

