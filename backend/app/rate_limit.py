"""
Simple in-memory rate limiting middleware for Hermes.
Limits requests per tenant/IP per minute.
"""

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, Tuple
from fastapi import Request, HTTPException, status
from app.config import settings


class RateLimiter:
    def __init__(self, requests_per_minute: int = 60):
        self.requests_per_minute = requests_per_minute
        # Store: (tenant_id, ip) -> list of timestamps
        self.requests: Dict[Tuple[str, str], list] = defaultdict(list)
    
    def is_allowed(self, tenant_id: str, ip: str) -> bool:
        key = (tenant_id, ip)
        now = datetime.utcnow()
        minute_ago = now - timedelta(minutes=1)
        
        # Clean old requests
        self.requests[key] = [
            ts for ts in self.requests[key] 
            if ts > minute_ago
        ]
        
        # Check if under limit
        if len(self.requests[key]) >= self.requests_per_minute:
            return False
        
        # Record this request
        self.requests[key].append(now)
        return True
    
    def cleanup(self):
        """Clean up old entries periodically"""
        now = datetime.utcnow()
        minute_ago = now - timedelta(minutes=1)
        
        for key in list(self.requests.keys()):
            self.requests[key] = [
                ts for ts in self.requests[key] 
                if ts > minute_ago
            ]
            if not self.requests[key]:
                del self.requests[key]


# Global rate limiter instance
rate_limiter = RateLimiter(settings.RATE_LIMIT_PER_MINUTE)


async def check_rate_limit(request: Request, tenant_id: str):
    """
    Dependency to check rate limits per tenant/IP.
    """
    # Get client IP (considering proxies)
    ip = request.headers.get("X-Forwarded-For", request.client.host)
    if ip:
        ip = ip.split(",")[0].strip()  # Take first IP if multiple
    
    if not rate_limiter.is_allowed(tenant_id, ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limit exceeded. Maximum {settings.RATE_LIMIT_PER_MINUTE} requests per minute."
        )
