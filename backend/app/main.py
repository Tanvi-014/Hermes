from contextlib import asynccontextmanager
import json
import logging
import os
from typing import Any, Dict, List, Optional
from uuid import UUID
from fastapi import FastAPI, Depends, HTTPException, Query, Request, Response, status, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, desc
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from starlette.middleware.httpsredirect import HTTPSRedirectMiddleware
from app.db import get_db, init_db
from app.rate_limit import check_rate_limit
from app.logging_config import configure_logging
from app.models import Webhook, WebhookStatus, DeliveryAttempt, AlertConfig, User, Project, ProjectMember
from app.auth import (
    get_password_hash,
    verify_password,
    create_access_token,
    get_current_user,
    get_current_active_project,
    require_project_role,
    get_tenant_from_auth,
)
from app.routing import apply_transform, event_matches_filter, extract_event_id
from app.schemas import (
    WebhookResponse,
    WebhookDetailResponse,
    DashboardStats,
    AlertConfigCreate,
    AlertConfigUpdate,
    AlertConfigResponse,
)
from app.security import require_api_key, validate_destination_url
from app.signatures import verify_webhook_signature
from app.worker import WorkerPool
from app.alerts import _send_slack_alert, _send_email_alert

configure_logging()
logger = logging.getLogger("hermes.api")

# Worker pool instance
worker_pool = WorkerPool(concurrency=settings.WORKER_CONCURRENCY)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Handles startup and shutdown events:
    1. Initializes DB tables.
    2. Starts the background worker pool.
    3. Gracefully shuts down the worker pool on termination.
    """
    if settings.AUTO_CREATE_TABLES:
        logger.info("Initializing database...")
        await init_db()
    else:
        logger.info("Skipping automatic table creation because AUTO_CREATE_TABLES=false")
    
    logger.info("Starting background worker pool...")
    worker_pool.start()
    
    yield
    
    logger.info("Shutting down background workers...")
    await worker_pool.stop()

app = FastAPI(
    title=settings.APP_NAME,
    description="High-reliability self-hostable webhook proxy & delivery manager.",
    version="1.0.0",
    lifespan=lifespan
)

# Enable CORS for frontend dashboard queries
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # In development, allow all origins.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTPS enforcement middleware (production only)
if settings.FORCE_HTTPS:
    app.add_middleware(HTTPSRedirectMiddleware)

# Headers to filter out during ingestion to prevent downstreams from getting confused
EXCLUDED_INGEST_HEADERS = {
    "host",
    "connection",
    "content-length",
    "accept-encoding",
    "user-agent",
    "x-real-ip",
    "x-forwarded-for",
    "x-forwarded-proto",
    "x-forwarded-port",
}

@app.post("/api/v1/ingest", status_code=status.HTTP_200_OK)
async def ingest_webhook(
    request: Request,
    url: Optional[str] = Query(None, description="The downstream destination URL for this webhook"),
    urls: Optional[List[str]] = Query(None, description="Additional downstream URLs for fan-out delivery"),
    filter_expression: Optional[str] = Query(None, alias="filter", description="Simple filter, for example event.type == 'payment.succeeded'"),
    transform: Optional[str] = Query(None, description="JSON object mapping output fields to source paths"),
    signature_provider: Optional[str] = Query(None, description="Optional signature provider: stripe, github, or hermes"),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db),
    _rate_limit: None = Depends(lambda: check_rate_limit(request, tenant_id))
):
    """
    Generic ingestion endpoint. Accepts any headers and body, writes immediately
    to Postgres, and returns a 200 OK so the sender assumes delivery succeeded.
    """
    destination_candidates: List[str] = []
    if url:
        destination_candidates.append(url)
    if urls:
        for item in urls:
            destination_candidates.extend([part.strip() for part in item.split(",") if part.strip()])

    if not destination_candidates:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one destination is required via url or urls",
        )

    destination_urls = [validate_destination_url(destination) for destination in destination_candidates]

    # 1. Capture raw body once so signature verification and JSON parsing use identical bytes.
    raw_body = await request.body()
    verify_webhook_signature(signature_provider, request, raw_body)

    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except Exception:
        # If payload isn't JSON, read raw body as text and wrap it
        payload = {"_raw_body": raw_body.decode("utf-8", errors="replace")}

    explicit_event_id = request.headers.get("X-Event-Id") or request.headers.get("X-Hermes-Event-Id")
    event_id = extract_event_id(payload, explicit_event_id)

    if not event_matches_filter(payload, filter_expression):
        logger.info(
            "Webhook filtered before queueing.",
            extra={
                "event": "webhook.ingest.filtered",
                "tenant_id": tenant_id,
                "event_id": event_id,
                "filter": filter_expression,
                "destination_count": len(destination_urls),
            },
        )
        return {
            "success": True,
            "filtered": True,
            "webhook_ids": [],
            "message": "Webhook did not match filter and was not queued",
        }

    delivery_payload = apply_transform(payload, transform)

    # 2. Extract and filter headers
    headers: Dict[str, str] = {}
    for key, val in request.headers.items():
        if key.lower() not in EXCLUDED_INGEST_HEADERS:
            headers[key] = val

    idempotency_key = request.headers.get("Idempotency-Key") or request.headers.get("X-Hermes-Idempotency-Key") or event_id
    webhook_ids: List[str] = []
    duplicate_ids: List[str] = []

    for destination_url in destination_urls:
        existing_result = await db.execute(
            select(Webhook).where(
                Webhook.tenant_id == tenant_id,
                Webhook.destination_url == destination_url,
                Webhook.idempotency_key == idempotency_key,
            )
        )
        existing_webhook = existing_result.scalar_one_or_none()
        if existing_webhook:
            webhook_ids.append(str(existing_webhook.id))
            duplicate_ids.append(str(existing_webhook.id))
            logger.info(
                "Duplicate webhook ingestion resolved by idempotency key.",
                extra={
                    "event": "webhook.ingest.duplicate",
                    "webhook_id": str(existing_webhook.id),
                    "tenant_id": tenant_id,
                    "event_id": event_id,
                    "destination_url": destination_url,
                    "idempotency_key": idempotency_key,
                },
            )
            continue

        webhook = Webhook(
            tenant_id=tenant_id,
            event_id=event_id,
            destination_url=destination_url,
            payload=delivery_payload,
            headers=headers,
            idempotency_key=idempotency_key,
            status=WebhookStatus.PENDING.value,
            max_retries=settings.DEFAULT_MAX_RETRIES
        )

        db.add(webhook)
        try:
            await db.flush()
            await db.commit()
            webhook_ids.append(str(webhook.id))
        except IntegrityError:
            await db.rollback()
            existing_result = await db.execute(
                select(Webhook).where(
                    Webhook.tenant_id == tenant_id,
                    Webhook.destination_url == destination_url,
                    Webhook.idempotency_key == idempotency_key,
                )
            )
            existing_webhook = existing_result.scalar_one_or_none()
            if not existing_webhook:
                raise
            webhook_ids.append(str(existing_webhook.id))
            duplicate_ids.append(str(existing_webhook.id))

        logger.info(
            "Webhook destination queued.",
            extra={
                "event": "webhook.ingest.destination_queued",
                "webhook_id": webhook_ids[-1],
                "tenant_id": tenant_id,
                "event_id": event_id,
                "destination_url": destination_url,
                "idempotency_key": idempotency_key,
            },
        )

    logger.info(
        "Webhook ingestion completed.",
        extra={
            "event": "webhook.ingest.completed",
            "tenant_id": tenant_id,
            "event_id": event_id,
            "destination_count": len(destination_urls),
            "queued_count": len(webhook_ids) - len(duplicate_ids),
            "duplicate_count": len(duplicate_ids),
            "signature_provider": signature_provider,
        },
    )
    
    # Return immediately to the client (200 OK)
    single = len(webhook_ids) == 1
    return {
        "success": True,
        "filtered": False,
        "event_id": event_id,
        "tenant_id": tenant_id,
        "webhook_id": webhook_ids[0] if single else None,
        "webhook_ids": webhook_ids,
        "duplicate": len(duplicate_ids) == len(webhook_ids),
        "duplicate_ids": duplicate_ids,
        "message": "Webhook ingested and queued for delivery"
    }

@app.get("/api/v1/webhooks", response_model=Dict[str, Any])
async def list_webhooks(
    status_filter: Optional[str] = Query(None, alias="status"),
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of all ingested webhooks, sorted by creation date descending.
    Allows filtering by status and supports pagination.
    """
    offset = (page - 1) * limit
    
    # 1. Build Query
    stmt = select(Webhook).order_by(desc(Webhook.created_at))
    count_stmt = select(func.count(Webhook.id))
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
        count_stmt = count_stmt.where(Webhook.tenant_id == tenant_id)
    
    if status_filter:
        try:
            status_enum = WebhookStatus(status_filter.lower())
            stmt = stmt.where(Webhook.status == status_enum.value)
            count_stmt = count_stmt.where(Webhook.status == status_enum.value)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid status value. Choose from: {[s.value for s in WebhookStatus]}"
            )

    # 2. Execute paginated select and count
    result = await db.execute(stmt.offset(offset).limit(limit))
    webhooks = result.scalars().all()
    
    total_count_result = await db.execute(count_stmt)
    total_count = total_count_result.scalar_one()

    return {
        "webhooks": [w.to_dict() for w in webhooks],
        "total": total_count,
        "page": page,
        "limit": limit,
        "total_pages": (total_count + limit - 1) // limit
    }

@app.get("/api/v1/webhooks/{webhook_id}", response_model=WebhookDetailResponse)
async def get_webhook_details(
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves full details for a single webhook, including its logs/attempts.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
    result = await db.execute(stmt)
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found"
        )
        
    return webhook

@app.post("/api/v1/webhooks/{webhook_id}/replay")
async def replay_webhook(
    webhook_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Manually replays a failed or dead-lettered webhook.
    Resets the attempt counter, sets status back to pending, and forces
    next_attempt_at to current timestamp.
    """
    stmt = select(Webhook).where(Webhook.id == webhook_id)
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)
    result = await db.execute(stmt)
    webhook = result.scalar_one_or_none()
    
    if not webhook:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found"
        )

    # Reset attempt tracking parameters
    webhook.status = WebhookStatus.PENDING.value
    webhook.retry_count = 0
    webhook.next_attempt_at = func.now()
    webhook.updated_at = func.now()
    
    await db.commit()
    logger.info(
        "Manual replay triggered.",
        extra={"event": "webhook.replay.requested", "webhook_id": str(webhook_id), "tenant_id": tenant_id, "event_id": webhook.event_id},
    )
    
    return {
        "success": True,
        "message": "Webhook rescheduled for immediate delivery attempt."
    }

@app.get("/api/v1/stats", response_model=DashboardStats)
async def get_stats(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Computes real-time statistics of webhook executions for the dashboard.
    """
    # 1. Total count
    tenant_filter = Webhook.tenant_id == tenant_id if settings.api_key_tenants else True
    total = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter)) or 0
    
    # 2. Count by status
    pending = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.FAILED.value)) or 0

    # 3. Calculate success rate based on terminal states (completed / (completed + failed))
    terminal_total = completed + failed
    success_rate = (completed / terminal_total * 100) if terminal_total > 0 else 100.0

    return {
        "total_webhooks": total,
        "pending_count": pending,
        "processing_count": processing,
        "completed_count": completed,
        "failed_count": failed,
        "success_rate": round(success_rate, 1)
    }

@app.get("/metrics")
async def get_metrics(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    tenant_filter = Webhook.tenant_id == tenant_id if settings.api_key_tenants else True
    total = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter)) or 0
    pending = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PENDING.value)) or 0
    processing = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.PROCESSING.value)) or 0
    completed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.COMPLETED.value)) or 0
    failed = await db.scalar(select(func.count(Webhook.id)).where(tenant_filter, Webhook.status == WebhookStatus.FAILED.value)) or 0
    attempts = await db.scalar(
        select(func.count(DeliveryAttempt.id))
        .join(Webhook, DeliveryAttempt.webhook_id == Webhook.id)
        .where(tenant_filter)
    ) or 0

    body = "\n".join([
        "# HELP hermes_webhooks_total Total ingested webhooks.",
        "# TYPE hermes_webhooks_total gauge",
        f"hermes_webhooks_total {total}",
        "# HELP hermes_webhooks_by_status Webhooks grouped by delivery status.",
        "# TYPE hermes_webhooks_by_status gauge",
        f'hermes_webhooks_by_status{{status="pending"}} {pending}',
        f'hermes_webhooks_by_status{{status="processing"}} {processing}',
        f'hermes_webhooks_by_status{{status="completed"}} {completed}',
        f'hermes_webhooks_by_status{{status="failed"}} {failed}',
        "# HELP hermes_delivery_attempts_total Total delivery attempts.",
        "# TYPE hermes_delivery_attempts_total gauge",
        f"hermes_delivery_attempts_total {attempts}",
        "",
    ])

    return Response(content=body, media_type="text/plain; version=0.0.4")

@app.get("/api/v1/usage")
async def get_usage(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(
            Webhook.tenant_id,
            func.count(Webhook.id).label("events"),
            func.count(func.distinct(Webhook.event_id)).label("unique_events"),
        )
        .group_by(Webhook.tenant_id)
        .order_by(Webhook.tenant_id)
    )
    if settings.api_key_tenants:
        stmt = stmt.where(Webhook.tenant_id == tenant_id)

    rows = (await db.execute(stmt)).all()
    return {
        "usage": [
            {
                "tenant_id": row.tenant_id,
                "events": row.events,
                "unique_events": row.unique_events,
            }
            for row in rows
        ]
    }

@app.get("/api/v1/alerts", response_model=List[AlertConfigResponse])
async def list_alerts(
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve all alert configurations scoped to the active tenant.
    """
    tenant_filter = AlertConfig.tenant_id == tenant_id if settings.api_key_tenants else True
    stmt = select(AlertConfig).where(tenant_filter).order_by(desc(AlertConfig.created_at))
    result = await db.execute(stmt)
    configs = result.scalars().all()
    return [c.to_dict() for c in configs]

@app.post("/api/v1/alerts", response_model=AlertConfigResponse, status_code=status.HTTP_201_CREATED)
async def create_alert(
    config_in: AlertConfigCreate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new alert configuration for the active tenant.
    """
    config = AlertConfig(
        tenant_id=tenant_id,
        name=config_in.name,
        channel_type=config_in.channel_type,
        config=config_in.config,
        enabled=config_in.enabled if config_in.enabled is not None else True
    )
    db.add(config)
    await db.flush()
    await db.commit()
    await db.refresh(config)
    return config.to_dict()

@app.get("/api/v1/alerts/{alert_id}", response_model=AlertConfigResponse)
async def get_alert(
    alert_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieve details of a single alert configuration.
    """
    stmt = select(AlertConfig).where(AlertConfig.id == alert_id)
    if settings.api_key_tenants:
        stmt = stmt.where(AlertConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert configuration not found")
    return config.to_dict()

@app.put("/api/v1/alerts/{alert_id}", response_model=AlertConfigResponse)
async def update_alert(
    alert_id: UUID,
    config_in: AlertConfigUpdate,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Update details of an existing alert configuration.
    Handles credential masking to prevent overwriting keys with placeholder text.
    """
    stmt = select(AlertConfig).where(AlertConfig.id == alert_id)
    if settings.api_key_tenants:
        stmt = stmt.where(AlertConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert configuration not found")
    
    if config_in.name is not None:
        config.name = config_in.name
    if config_in.config is not None:
        new_config = {**config_in.config}
        existing_config = config.config or {}
        
        # Prevent placeholder strings from overwriting real secrets
        for sensitive_key in ("password", "smtp_password"):
            if new_config.get(sensitive_key) == "••••••••" and sensitive_key in existing_config:
                new_config[sensitive_key] = existing_config[sensitive_key]
        
        if "webhook_url" in new_config and new_config["webhook_url"].startswith("…") and "webhook_url" in existing_config:
            new_config["webhook_url"] = existing_config["webhook_url"]
            
        config.config = new_config

    if config_in.enabled is not None:
        config.enabled = config_in.enabled
    
    config.updated_at = func.now()
    await db.commit()
    await db.refresh(config)
    return config.to_dict()

@app.delete("/api/v1/alerts/{alert_id}")
async def delete_alert(
    alert_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete an alert configuration.
    """
    stmt = select(AlertConfig).where(AlertConfig.id == alert_id)
    if settings.api_key_tenants:
        stmt = stmt.where(AlertConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert configuration not found")
    
    await db.delete(config)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

@app.post("/api/v1/alerts/{alert_id}/test")
async def test_alert(
    alert_id: UUID,
    tenant_id: str = Depends(get_tenant_from_auth),
    db: AsyncSession = Depends(get_db)
):
    """
    Send a dummy DLQ alert immediately using the specified configuration to verify connection credentials.
    """
    stmt = select(AlertConfig).where(AlertConfig.id == alert_id)
    if settings.api_key_tenants:
        stmt = stmt.where(AlertConfig.tenant_id == tenant_id)
    result = await db.execute(stmt)
    config = result.scalar_one_or_none()
    if not config:
        raise HTTPException(status_code=404, detail="Alert configuration not found")
    
    # Generate dummy test data
    test_data = {
        "webhook_id": "00000000-0000-0000-0000-000000000000",
        "event_id": "evt_test_123456",
        "destination_url": "https://example.com/webhook-receiver",
        "retry_count": 5,
        "last_error": "HTTP Error Status 500: Internal Server Error",
        "tenant_id": tenant_id,
    }
    
    try:
        if config.channel_type == "slack":
            await _send_slack_alert(config, test_data)
        elif config.channel_type == "email":
            await _send_email_alert(config, test_data)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported channel type: {config.channel_type}")
    except Exception as e:
        logger.error(f"Test alert failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Failed to send test alert: {str(e)}")
        
    return {"success": True, "message": f"Test alert successfully sent to {config.name}."}

@app.get("/health")
async def health_check():
    return {"status": "healthy", "version": "1.0.0"}

@app.get("/health/detailed")
async def detailed_health_check(db: AsyncSession = Depends(get_db)):
    """Detailed health check including database connectivity."""
    try:
        # Check database connection
        await db.execute(select(func.count()))
        db_status = "healthy"
    except Exception:
        db_status = "unhealthy"
    
    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "version": "1.0.0",
        "components": {
            "database": db_status,
            "api": "healthy"
        }
    }


# Auth endpoints
@app.post("/api/v1/auth/register", status_code=status.HTTP_201_CREATED)
async def register(
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db)
):
    """
    Register a new user account.
    """
    # Check if user already exists
    existing_result = await db.execute(select(User).where(User.email == email))
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )
    
    # Create new user
    user = User(
        email=email,
        password_hash=get_password_hash(password)
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    
    logger.info(
        "User registered",
        extra={"event": "user.registered", "user_id": str(user.id), "email": email}
    )
    
    return {"message": "User registered successfully", "user_id": str(user.id)}


@app.post("/api/v1/auth/login")
async def login(
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
    db: AsyncSession = Depends(get_db)
):
    """
    Login and receive a JWT access token.
    """
    result = await db.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    
    if not user or not verify_password(password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )
    
    access_token = create_access_token(data={"sub": str(user.id)})
    
    logger.info(
        "User logged in",
        extra={"event": "user.login", "user_id": str(user.id), "email": email}
    )
    
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": user.to_dict()
    }


@app.get("/api/v1/auth/me")
async def get_me(current_user: User = Depends(get_current_user)):
    """
    Get current user information.
    """
    return current_user.to_dict()


# Project endpoints
@app.get("/api/v1/projects", response_model=List[Dict[str, Any]])
async def list_projects(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all projects the current user has access to.
    """
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(ProjectMember.user_id == current_user.id)
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()
    
    # Add user's role for each project
    projects_with_role = []
    for project in projects:
        member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == current_user.id
            )
        )
        member = member_result.scalar_one_or_none()
        project_dict = project.to_dict()
        project_dict["role"] = member.role if member else None
        projects_with_role.append(project_dict)
    
    return projects_with_role


@app.post("/api/v1/projects", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def create_project(
    name: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Create a new project and add the current user as owner.
    """
    import uuid
    
    project = Project(
        name=name,
        api_key=f"hk_live_{uuid.uuid4().hex}"
    )
    db.add(project)
    await db.flush()
    
    # Add user as owner
    member = ProjectMember(
        project_id=project.id,
        user_id=current_user.id,
        role="owner"
    )
    db.add(member)
    
    await db.commit()
    await db.refresh(project)
    
    logger.info(
        "Project created",
        extra={"event": "project.created", "project_id": str(project.id), "user_id": str(current_user.id)}
    )
    
    project_dict = project.to_dict()
    project_dict["role"] = "owner"
    return project_dict


@app.get("/api/v1/projects/{project_id}", response_model=Dict[str, Any])
async def get_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Get details of a specific project.
    """
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    # Verify user has access
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id
        )
    )
    member = member_result.scalar_one_or_none()
    if not member:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project"
        )
    
    project_dict = project.to_dict()
    project_dict["role"] = member.role
    return project_dict


@app.put("/api/v1/projects/{project_id}", response_model=Dict[str, Any])
async def update_project(
    project_id: UUID,
    name: Optional[str] = Body(None, embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update project details (owner/admin only).
    """
    require_owner_admin = require_project_role(required_roles=["owner", "admin"])
    member = await require_owner_admin(project_id=project_id, current_user=current_user, db=db)
    
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    if name is not None:
        project.name = name
    
    await db.commit()
    await db.refresh(project)
    
    project_dict = project.to_dict()
    project_dict["role"] = member.role
    return project_dict


@app.delete("/api/v1/projects/{project_id}")
async def delete_project(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Delete a project (owner only).
    """
    require_owner = require_project_role(required_roles=["owner"])
    await require_owner(project_id=project_id, current_user=current_user, db=db)
    
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Project not found"
        )
    
    await db.delete(project)
    await db.commit()
    
    logger.info(
        "Project deleted",
        extra={"event": "project.deleted", "project_id": str(project_id), "user_id": str(current_user.id)}
    )
    
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# Team member endpoints
@app.get("/api/v1/projects/{project_id}/members", response_model=List[Dict[str, Any]])
async def list_project_members(
    project_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    List all members of a project.
    """
    # Verify user has access
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == current_user.id
        )
    )
    if not member_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have access to this project"
        )
    
    result = await db.execute(
        select(ProjectMember).where(ProjectMember.project_id == project_id)
    )
    members = result.scalars().all()
    
    return [m.to_dict() for m in members]


@app.post("/api/v1/projects/{project_id}/members", response_model=Dict[str, Any], status_code=status.HTTP_201_CREATED)
async def add_project_member(
    project_id: UUID,
    email: str = Body(..., embed=True),
    role: str = Body("viewer", embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Add a member to a project (owner/admin only).
    """
    if role not in ["owner", "admin", "viewer"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be one of: owner, admin, viewer"
        )
    
    require_owner_admin = require_project_role(required_roles=["owner", "admin"])
    await require_owner_admin(project_id=project_id, current_user=current_user, db=db)
    
    # Find user by email
    user_result = await db.execute(select(User).where(User.email == email))
    user = user_result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found. Ask them to register first."
        )
    
    # Check if already a member
    existing_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id
        )
    )
    if existing_result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="User is already a member of this project"
        )
    
    # Add member
    member = ProjectMember(
        project_id=project_id,
        user_id=user.id,
        role=role
    )
    db.add(member)
    await db.commit()
    await db.refresh(member)
    
    logger.info(
        "Project member added",
        extra={"event": "project.member_added", "project_id": str(project_id), "user_id": str(user.id), "role": role}
    )
    
    return member.to_dict()


@app.put("/api/v1/projects/{project_id}/members/{user_id}", response_model=Dict[str, Any])
async def update_project_member_role(
    project_id: UUID,
    user_id: UUID,
    role: str = Body(..., embed=True),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Update a member's role (owner only).
    """
    if role not in ["owner", "admin", "viewer"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role must be one of: owner, admin, viewer"
        )
    
    require_owner = require_project_role(required_roles=["owner"])
    await require_owner(project_id=project_id, current_user=current_user, db=db)
    
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )
    
    # Prevent removing the last owner
    if member.role == "owner" and role != "owner":
        owner_count_result = await db.execute(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project_id,
                ProjectMember.role == "owner"
            )
        )
        owner_count = owner_count_result.scalar()
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner from a project"
            )
    
    member.role = role
    await db.commit()
    await db.refresh(member)
    
    logger.info(
        "Project member role updated",
        extra={"event": "project.member_role_updated", "project_id": str(project_id), "user_id": str(user_id), "role": role}
    )
    
    return member.to_dict()


@app.delete("/api/v1/projects/{project_id}/members/{user_id}")
async def remove_project_member(
    project_id: UUID,
    user_id: UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Remove a member from a project (owner only).
    """
    require_owner = require_project_role(required_roles=["owner"])
    await require_owner(project_id=project_id, current_user=current_user, db=db)
    
    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Member not found"
        )
    
    # Prevent removing the last owner
    if member.role == "owner":
        owner_count_result = await db.execute(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project_id,
                ProjectMember.role == "owner"
            )
        )
        owner_count = owner_count_result.scalar()
        if owner_count <= 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Cannot remove the last owner from a project"
            )
    
    await db.delete(member)
    await db.commit()
    
    logger.info(
        "Project member removed",
        extra={"event": "project.member_removed", "project_id": str(project_id), "user_id": str(user_id)}
    )
    
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# Mount static frontend files path-safely
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
FRONTEND_DIR = os.path.abspath(os.path.join(CURRENT_DIR, "..", "..", "frontend"))
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
