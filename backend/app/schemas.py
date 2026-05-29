from datetime import datetime
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field
from uuid import UUID

class DeliveryAttemptResponse(BaseModel):
    id: UUID
    webhook_id: UUID
    attempt_number: int
    status_code: Optional[int] = None
    response_body: Optional[str] = None
    duration_ms: Optional[int] = None
    error_message: Optional[str] = None
    attempted_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookResponse(BaseModel):
    id: UUID
    tenant_id: str
    event_id: str
    destination_url: str
    idempotency_key: Optional[str] = None
    status: str
    retry_count: int
    max_retries: int
    next_attempt_at: Optional[datetime] = None
    last_attempt_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class WebhookDetailResponse(WebhookResponse):
    payload: Any
    headers: Dict[str, str]
    attempts: List[DeliveryAttemptResponse] = []

    model_config = ConfigDict(from_attributes=True)

class DashboardStats(BaseModel):
    total_webhooks: int
    pending_count: int
    processing_count: int
    completed_count: int
    failed_count: int  # DLQ
    success_rate: float


class AlertConfigCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    channel_type: str = Field(..., pattern="^(slack|email)$")
    config: Dict[str, Any]
    enabled: Optional[bool] = True


class AlertConfigUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    config: Optional[Dict[str, Any]] = None
    enabled: Optional[bool] = None


class AlertConfigResponse(BaseModel):
    id: UUID
    tenant_id: str
    name: str
    channel_type: str
    config: Dict[str, Any]
    enabled: bool
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

