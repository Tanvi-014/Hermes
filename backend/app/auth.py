from datetime import datetime, timedelta
from typing import Optional
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db import get_db
from app.models import User, Project, ProjectMember

# Use argon2 to avoid bcrypt 72-byte password limitation
pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
security = HTTPBearer()


async def require_api_key(request: Request) -> str:
    """Legacy API key auth for backward compatibility."""
    tenants = settings.api_key_tenants
    if not tenants:
        return "anonymous"

    supplied_key = request.headers.get("X-Hermes-API-Key")
    tenant_id = tenants.get(supplied_key or "")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Hermes API key",
        )
    return tenant_id


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    # Truncate password to 72 bytes max for bcrypt compatibility
    if len(password.encode('utf-8')) > 72:
        password = password[:72]
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=7)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
    return encoded_jwt


def decode_access_token(token: str) -> dict:
    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: AsyncSession = Depends(get_db)
) -> User:
    token = credentials.credentials
    payload = decode_access_token(token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id: str = payload.get("sub")
    if user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return user


async def get_current_active_project(
    current_user: User = Depends(get_current_user),
    project_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
) -> Project:
    """
    Get the current project for the authenticated user.
    If project_id is provided, verify user has access to that project.
    Otherwise, return the user's first project.
    """
    if project_id:
        result = await db.execute(
            select(Project).where(Project.id == project_id)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found"
            )
        
        # Verify user has access to this project
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
        
        return project
    else:
        # Get user's first project
        result = await db.execute(
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(ProjectMember.user_id == current_user.id)
            .limit(1)
        )
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="No projects found for user"
            )
        return project


async def require_project_role(
    required_roles: list[str] = ["owner", "admin", "viewer"]
):
    """
    Dependency factory to require specific project roles.
    """
    async def role_checker(
        current_user: User = Depends(get_current_user),
        project_id: Optional[str] = None,
        db: AsyncSession = Depends(get_db)
    ) -> ProjectMember:
        if not project_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="project_id is required"
            )
        
        result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id
            )
        )
        member = result.scalar_one_or_none()
        
        if not member:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="You do not have access to this project"
            )
        
        if member.role not in required_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Insufficient permissions. Required roles: {required_roles}"
            )
        
        return member
    
    return role_checker


async def get_tenant_from_auth(
    request: Request,
    db: AsyncSession = Depends(get_db)
) -> str:
    """
    Get tenant_id from either JWT token (SaaS mode) or API key (legacy mode).
    Returns the project.api_key as tenant_id for JWT auth, or the mapped tenant for API key auth.
    """
    # Try JWT auth first
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        try:
            from app.auth import decode_access_token, get_current_user
            token = auth_header.split(" ")[1]
            payload = decode_access_token(token)
            if payload:
                user_id = payload.get("sub")
                if user_id:
                    result = await db.execute(select(User).where(User.id == user_id))
                    user = result.scalar_one_or_none()
                    if user:
                        # Get project_id from query param or header
                        project_id = request.query_params.get("project_id") or request.headers.get("X-Project-Id")
                        if project_id:
                            # Verify user has access to this project
                            member_result = await db.execute(
                                select(ProjectMember).where(
                                    ProjectMember.project_id == project_id,
                                    ProjectMember.user_id == user.id
                                )
                            )
                            member = member_result.scalar_one_or_none()
                            if member:
                                project_result = await db.execute(select(Project).where(Project.id == project_id))
                                project = project_result.scalar_one_or_none()
                                if project:
                                    return project.api_key
        except Exception:
            pass  # Fall back to API key auth
    
    # Fall back to API key auth
    return await require_api_key(request)
