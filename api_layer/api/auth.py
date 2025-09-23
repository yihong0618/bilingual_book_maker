"""
Authentication and authorization middleware for the Bilingual Book Maker API
Simple API key-based authentication suitable for small-scale deployment
"""

import hashlib
import secrets
import time
from typing import Optional, Dict, Set
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from datetime import datetime, timedelta
import logging

from .config.constants import HttpStatusConstants, SecurityConstants, ValidationConstants

logger = logging.getLogger(__name__)

# Simple in-memory API key store (replace with database for production)
# Format: {api_key_hash: {"created": datetime, "name": str, "usage_count": int, "rate_limit": int}}
API_KEYS: Dict[str, dict] = {}

# Rate limiting storage (replace with Redis for production)
# Format: {api_key_hash: {"requests": int, "reset_time": datetime}}
RATE_LIMITS: Dict[str, dict] = {}


class APIKeyManager:
    """Manages API keys and authentication"""

    @staticmethod
    def hash_api_key(api_key: str) -> str:
        """Hash API key for secure storage"""
        return hashlib.sha256(api_key.encode()).hexdigest()

    @staticmethod
    def generate_api_key() -> str:
        """Generate a secure API key"""
        return f"bbm_{secrets.token_urlsafe(32)}"

    @staticmethod
    def create_api_key(name: str, rate_limit_per_hour: int = 60) -> str:
        """Create a new API key with given name and rate limit"""
        api_key = APIKeyManager.generate_api_key()
        api_key_hash = APIKeyManager.hash_api_key(api_key)

        API_KEYS[api_key_hash] = {
            "created": datetime.now(),
            "name": name,
            "usage_count": 0,
            "rate_limit_per_hour": rate_limit_per_hour,
            "active": True
        }

        logger.info(f"Created API key for: {name}")
        return api_key

    @staticmethod
    def validate_api_key(api_key: str) -> Optional[dict]:
        """Validate API key and return key info if valid"""
        api_key_hash = APIKeyManager.hash_api_key(api_key)

        if api_key_hash not in API_KEYS:
            return None

        key_info = API_KEYS[api_key_hash]
        if not key_info.get("active", True):
            return None

        return key_info

    @staticmethod
    def check_rate_limit(api_key: str) -> bool:
        """Check if API key is within rate limits"""
        api_key_hash = APIKeyManager.hash_api_key(api_key)

        # Get key info for rate limit
        key_info = API_KEYS.get(api_key_hash)
        if not key_info:
            return False

        rate_limit = key_info.get("rate_limit_per_hour", 60)
        now = datetime.now()

        # Initialize or check rate limit data
        if api_key_hash not in RATE_LIMITS:
            RATE_LIMITS[api_key_hash] = {
                "requests": 0,
                "reset_time": now + timedelta(hours=1)
            }

        rate_data = RATE_LIMITS[api_key_hash]

        # Reset if time window expired
        if now >= rate_data["reset_time"]:
            rate_data["requests"] = 0
            rate_data["reset_time"] = now + timedelta(hours=1)

        # Check if within limit
        if rate_data["requests"] >= rate_limit:
            return False

        # Increment request count
        rate_data["requests"] += 1
        key_info["usage_count"] += 1

        return True

    @staticmethod
    def revoke_api_key(api_key: str) -> bool:
        """Revoke an API key"""
        api_key_hash = APIKeyManager.hash_api_key(api_key)

        if api_key_hash in API_KEYS:
            API_KEYS[api_key_hash]["active"] = False
            logger.info(f"Revoked API key: {API_KEYS[api_key_hash]['name']}")
            return True
        return False

    @staticmethod
    def list_api_keys() -> Dict[str, dict]:
        """List all API keys (without the actual keys)"""
        return {
            key_hash[:8] + "...": {
                "name": info["name"],
                "created": info["created"],
                "usage_count": info["usage_count"],
                "active": info["active"],
                "rate_limit_per_hour": info["rate_limit_per_hour"]
            }
            for key_hash, info in API_KEYS.items()
        }


class AuthModels:
    """Pydantic models for authentication"""

    class APIKeyCreate(BaseModel):
        name: str
        rate_limit_per_hour: int = 60

    class APIKeyResponse(BaseModel):
        api_key: str
        name: str
        rate_limit_per_hour: int
        message: str


# FastAPI security scheme
security = HTTPBearer(auto_error=False)


async def get_current_api_key(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> str:
    """
    FastAPI dependency to validate API key authentication
    """
    # Check for API key in Authorization header
    if credentials and credentials.scheme.lower() == "bearer":
        api_key = credentials.credentials
    else:
        # Also check for API key in query parameter (less secure, for testing)
        # This should be disabled in production
        raise HTTPException(
            status_code=HttpStatusConstants.UNAUTHORIZED,
            detail="Missing or invalid API key. Use Authorization: Bearer <api_key>",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Validate API key format
    if not api_key.startswith("bbm_") or len(api_key) < 40:
        raise HTTPException(
            status_code=HttpStatusConstants.UNAUTHORIZED,
            detail="Invalid API key format",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Validate API key exists and is active
    key_info = APIKeyManager.validate_api_key(api_key)
    if not key_info:
        raise HTTPException(
            status_code=HttpStatusConstants.UNAUTHORIZED,
            detail="Invalid or revoked API key",
            headers={"WWW-Authenticate": "Bearer"}
        )

    # Check rate limits
    if not APIKeyManager.check_rate_limit(api_key):
        raise HTTPException(
            status_code=429,  # Too Many Requests
            detail="Rate limit exceeded. Please try again later.",
            headers={
                "Retry-After": "3600",  # 1 hour
                "X-RateLimit-Limit": str(key_info.get("rate_limit_per_hour", 60)),
                "X-RateLimit-Remaining": "0"
            }
        )

    return api_key


async def auth_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[str]:
    """
    Optional authentication - returns None if no credentials provided
    Used for endpoints that can work with or without auth
    """
    if not credentials:
        return None

    try:
        return await get_current_api_key(credentials)
    except HTTPException:
        return None


def init_demo_api_keys():
    """Initialize demo API keys for testing (remove in production)"""
    if not API_KEYS:  # Only create if empty
        # Create a demo API key for testing
        demo_key = APIKeyManager.create_api_key("demo_user", rate_limit_per_hour=100)
        logger.info(f"Demo API key created: {demo_key}")

        # Create an admin key with higher limits
        admin_key = APIKeyManager.create_api_key("admin", rate_limit_per_hour=1000)
        logger.info(f"Admin API key created: {admin_key}")


# Security middleware for additional headers
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses"""
    response = await call_next(request)

    # Add security headers
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'self'"

    return response