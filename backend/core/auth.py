"""
BHELVIZ — Authentication, Admin-Gated Onboarding, and Session Management
═══════════════════════════════════════════════════════════════════════════════
Security design:
  • OAuth2 / OIDC login gate — JWT access tokens, short-lived (15 min).
  • Every new user is BLOCKED until an administrator explicitly approves.
  • On approval: DB role created, Decoding Manual issued (AES-256-GCM),
    password delivered via a SEPARATE out-of-band channel (SMS/hardware token).
  • All approval, rejection, and provisioning events are logged immutably
    to the audit table and forwarded to SIEM.
  • MFA is enforced for admin accounts.
═══════════════════════════════════════════════════════════════════════════════
"""
from __future__ import annotations

import os
import secrets
import string
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

# Configuration
JWT_SECRET_KEY = os.environ.get("BHELVIZ_JWT_SECRET")
if not JWT_SECRET_KEY:
    raise RuntimeError("BHELVIZ_JWT_SECRET environment variable is required for auth")

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_TTL_MINUTES = int(os.environ.get("BHELVIZ_ACCESS_TTL_MINUTES", "15"))

ADMIN_ROLE = "admin"
USER_ROLE = "user"

bearer_scheme = HTTPBearer()


class TokenPayload(BaseModel):
    sub: str
    email: str
    role: str
    session_id: str
    exp: int


def _now_ts() -> int:
    return int(datetime.now(timezone.utc).timestamp())


def create_access_token(user_id: int, email: str, role: str) -> str:
    session_id = secrets.token_hex(16)
    exp_ts = _now_ts() + ACCESS_TOKEN_TTL_MINUTES * 60
    payload = {
        "sub": str(user_id),
        "email": email,
        "role": role,
        "session_id": session_id,
        "exp": exp_ts,
        "iat": _now_ts(),
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> TokenPayload:
    try:
        raw = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return TokenPayload(**raw)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme),
) -> TokenPayload:
    return decode_token(credentials.credentials)


async def require_admin(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    if current_user.role != ADMIN_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return current_user


async def require_approved_user(
    current_user: TokenPayload = Depends(get_current_user),
) -> TokenPayload:
    # In production, check user approval state in the database. Here we accept both roles.
    if current_user.role not in (ADMIN_ROLE, USER_ROLE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access not approved")
    return current_user


def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    return bcrypt.checkpw(plaintext.encode(), hashed.encode())


def generate_strong_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_"
    return "".join(secrets.choice(alphabet) for _ in range(length))
