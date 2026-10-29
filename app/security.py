import hashlib
import hmac
import secrets
import time
from collections import defaultdict
from typing import Dict, List

from fastapi import Request
from app.config import settings

SESSION_COOKIE_NAME = "predaccure_sso_session"
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_WINDOW_SECONDS = 900  # 15 minutes

_failed_attempts: Dict[str, List[float]] = defaultdict(list)


def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client and request.client.host:
        return request.client.host
    return "unknown"


def is_rate_limited(ip: str) -> bool:
    now = time.time()
    recent = [ts for ts in _failed_attempts[ip] if now - ts < LOCKOUT_WINDOW_SECONDS]
    _failed_attempts[ip] = recent
    return len(recent) >= MAX_LOGIN_ATTEMPTS


def record_failed_attempt(ip: str) -> int:
    now = time.time()
    recent = [ts for ts in _failed_attempts[ip] if now - ts < LOCKOUT_WINDOW_SECONDS]
    recent.append(now)
    _failed_attempts[ip] = recent
    return max(0, MAX_LOGIN_ATTEMPTS - len(recent))


def clear_failed_attempts(ip: str) -> None:
    _failed_attempts.pop(ip, None)


def verify_pin(candidate: str) -> bool:
    if not settings.auth_enabled:
        return True
    expected = settings.app_pin.strip().encode("utf-8")
    provided = (candidate or "").strip().encode("utf-8")
    return secrets.compare_digest(expected, provided)


def create_session_token() -> str:
    expires_at = int(time.time()) + settings.session_max_age_seconds
    nonce = secrets.token_hex(8)
    payload = f"v1.{expires_at}.{nonce}"
    sig = hmac.new(
        hashlib.sha256(settings.app_pin.strip().encode("utf-8")).digest(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str) -> bool:
    if not settings.auth_enabled:
        return True
    if not token:
        return False
    parts = token.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return False
    try:
        expires_at = int(parts[1])
    except ValueError:
        return False
    if time.time() > expires_at:
        return False

    payload = f"{parts[0]}.{parts[1]}.{parts[2]}"
    expected_sig = hmac.new(
        hashlib.sha256(settings.app_pin.strip().encode("utf-8")).digest(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return secrets.compare_digest(expected_sig, parts[3])


def is_request_authenticated(request: Request) -> bool:
    if not settings.auth_enabled:
        return True

    # 1. Check signed HttpOnly session cookie
    cookie_token = request.cookies.get(SESSION_COOKIE_NAME, "")
    if cookie_token and verify_session_token(cookie_token):
        return True

    # 2. Check X-App-Pin or Authorization Bearer header for programmatic API access
    header_pin = request.headers.get("x-app-pin", "")
    if header_pin and verify_pin(header_pin):
        return True

    auth_header = request.headers.get("authorization", "")
    if auth_header.lower().startswith("bearer "):
        bearer_pin = auth_header[7:].strip()
        if verify_pin(bearer_pin):
            return True

    return False
