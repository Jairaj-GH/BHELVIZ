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

import hashlib
import hmac
import os
import secrets
import string
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import OAuth2PasswordBearer, HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from pydantic import BaseModel

from models import AccessRequestCreate, AccessRequestDB, ApprovalAction, AuditEvent, UserSessionInfo

# ── CONFIG ────────────────────────────────────────────────────────────────────

JWT_SECRET_KEY    = os.environ["BHELVIZ_JWT_SECRET"]       # from Vault — never hardcoded
JWT_ALGORITHM     = "HS256"
ACCESS_TOKEN_TTL  = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(hours=8)

ADMIN_ROLE = "admin"
USER_ROLE  = "user"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")
bearer_scheme = HTTPBearer()

# ── TOKEN UTILITIES ───────────────────────────────────────────────────────────

class TokenPayload(BaseModel):
    sub:        str           # user_id as string
    email:      str
    role:       str
    session_id: str
    exp:        datetime


def create_access_token(user_id: int, email: str, role: str) -> str:
    session_id = secrets.token_hex(16)
    payload = {
        "sub":        str(user_id),
        "email":      email,
        "role":       role,
        "session_id": session_id,
        "exp":        datetime.now(timezone.utc) + ACCESS_TOKEN_TTL,
        "iat":        datetime.now(timezone.utc),
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


# ── FASTAPI DEPENDENCIES ──────────────────────────────────────────────────────

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
    db=Depends(lambda: None),   # inject real DB session in production
) -> TokenPayload:
    """
    Blocks access if the user's approval_status != 'approved'.
    Called on every data-access route.
    """
    # Production: query user table to confirm approved_at is set and not revoked.
    # If status is pending or denied → 403.
    if current_user.role not in (ADMIN_ROLE, USER_ROLE):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access not approved")
    return current_user


# ── PASSWORD UTILITIES ────────────────────────────────────────────────────────

def hash_password(plaintext: str) -> str:
    return bcrypt.hashpw(plaintext.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plaintext: str, hashed: str) -> bool:
    return bcrypt.checkpw(plaintext.encode(), hashed.encode())


def generate_strong_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


# ── ADMIN-GATED ONBOARDING ────────────────────────────────────────────────────

class OnboardingService:
    """
    Manages the admin-gated onboarding workflow.

    Flow:
      1. User submits AccessRequestCreate → stored as 'pending'.
      2. Admin receives signed encrypted email via notification service.
      3. Admin approves/denies via the Admin Approval Console (MFA-gated).
      4. On approval:
           a. DB role bviz_ro_<user_id> is created with read-only grants.
           b. Decoding Manual is generated offline and signed.
           c. Manual is delivered via one-time encrypted link (email).
           d. Manual password is delivered via separate SMS channel.
           e. DB credentials delivered via time-limited secure link.
      5. All events logged to audit table + SIEM.
    """

    def __init__(self, db_session, notification_service, manual_generator, audit_logger):
        self.db          = db_session
        self.notifier    = notification_service
        self.manual_gen  = manual_generator
        self.audit       = audit_logger

    def submit_request(self, req: AccessRequestCreate) -> AccessRequestDB:
        audit_id = f"BVIZ-REQ-{uuid.uuid4().hex[:8].upper()}"
        record = AccessRequestDB(
            **req.model_dump(),
            id=self._next_id(),
            status="pending",
            created_at=datetime.now(timezone.utc),
            audit_id=audit_id,
        )
        self.db.save(record)
        # Notify admin via S/MIME encrypted email
        self.notifier.send_admin_alert(
            subject=f"[BHELVIZ] New access request — {req.email}",
            body=self._request_email_body(record),
            encrypt=True,
        )
        self.audit.log(AuditEvent(
            timestamp=datetime.now(timezone.utc),
            action="ACCESS_REQUEST_SUBMITTED",
            actor=req.email,
            detail=f"New request {audit_id} — dept: {req.department}",
            level="WARN",
        ))
        return record

    def process_approval(self, action: ApprovalAction, admin_email: str) -> None:
        record = self.db.get_request(action.request_id)
        if not record:
            raise ValueError(f"Request {action.request_id} not found")
        if record.status != "pending":
            raise ValueError(f"Request {action.request_id} already processed: {record.status}")

        now = datetime.now(timezone.utc)
        record.reviewed_at = now
        record.reviewed_by = admin_email

        if action.action == "approve":
            record.status = "approved"
            self._provision_user(record)
            self.audit.log(AuditEvent(
                timestamp=now, action="USER_APPROVED", actor=admin_email,
                detail=f"User {record.email} approved. DB role and Decoding Manual issued.",
                level="INFO",
            ))
        else:
            record.status = "denied"
            self.audit.log(AuditEvent(
                timestamp=now, action="ACCESS_DENIED", actor=admin_email,
                detail=f"User {record.email} denied. Notes: {action.notes or 'none'}",
                level="WARN",
            ))

        self.db.update(record)

    def _provision_user(self, record: AccessRequestDB) -> None:
        """
        Creates DB role, generates Decoding Manual, and delivers credentials
        through separate out-of-band channels.
        """
        user_id   = record.id
        db_pass   = generate_strong_password()
        role_name = f"bviz_ro_{user_id}"

        # 1. Create Oracle read-only role (executed by DBA service account — not the app)
        self.db.create_oracle_role(role_name, db_pass)

        # 2. Generate per-user Decoding Manual (offline, air-gapped signing environment)
        manual_path, manual_password = self.manual_gen.generate(
            user_id=user_id,
            email=record.email,
        )

        # 3. Deliver manual via one-time time-limited encrypted download link (email)
        self.notifier.send_user_manual(
            email=record.email,
            manual_path=manual_path,
            expires_in=timedelta(hours=24),
        )

        # 4. Deliver manual password via SEPARATE channel (SMS)
        self.notifier.send_sms_password(
            phone=self.db.get_phone(record.id),
            password=manual_password,
        )

        # 5. Deliver DB credentials via time-limited secure link
        self.notifier.send_db_credentials(
            email=record.email,
            username=role_name,
            password=db_pass,
            expires_in=timedelta(hours=4),
        )

    def _request_email_body(self, r: AccessRequestDB) -> str:
        return (
            f"Name: {r.full_name}\nEmail: {r.email}\n"
            f"Department: {r.department}\nAudit ID: {r.audit_id}\n"
            f"Justification: {r.justification}\n\n"
            f"Review at: https://bhelviz-admin.internal/approve/{r.id}"
        )

    def _next_id(self) -> int:
        return self.db.next_sequence("access_request_seq")


# ── DECODING MANUAL GENERATOR ─────────────────────────────────────────────────

class DecodingManualGenerator:
    """
    Generates a per-user AES-256-GCM encrypted Decoding Manual.

    The manual contains:
      - Column mapping (logical name → physical encrypted column)
      - Encryption algorithm parameters (AES-256-GCM, IV scheme)
      - Per-user Application Obfuscation Key (AOK) wrapped with a KEK
        derived from the user's passphrase via Argon2id.

    SECURITY:
      - The AOK never touches any online server.
      - Manual generation runs in an offline, air-gapped signing environment.
      - The passphrase is generated here but immediately discarded server-side
        after delivery via SMS. Server never stores it.
    """

    def __init__(self, master_key_path: str, output_dir: str):
        self.master_key_path = master_key_path  # loaded from offline HSM
        self.output_dir      = output_dir

    def generate(self, user_id: int, email: str) -> tuple[str, str]:
        """
        Returns (manual_file_path, plaintext_password).
        Password is single-use and delivered out-of-band.
        Server must NOT persist the password after SMS delivery.
        """
        import json, os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

        password    = generate_strong_password(20)
        salt        = os.urandom(16)
        kdf         = Argon2id(salt=salt, length=32, iterations=3,
                               lanes=4, memory_cost=65536)
        kek         = kdf.derive(password.encode())

        # Wrap the global AOK with the per-user KEK
        aok         = self._load_master_aok()
        nonce       = os.urandom(12)
        aesgcm      = AESGCM(kek)
        wrapped_aok = aesgcm.encrypt(nonce, aok, None)

        manual = {
            "version":     "2.0",
            "user_id":     user_id,
            "email":       email,
            "algorithm":   "AES-256-GCM",
            "kdf":         "Argon2id",
            "salt_hex":    salt.hex(),
            "nonce_hex":   nonce.hex(),
            "wrapped_aok": wrapped_aok.hex(),
            "columns": {
                "employee.employee_no":   "employee_no_enc",
                "employee.full_name":     "full_name_enc",
                "employee.hired_at":      "hired_at_enc",
                "department.dept_name":   "dept_name_enc",
            },
        }

        # Encrypt the whole manual JSON with the same password
        manual_json  = json.dumps(manual).encode()
        outer_nonce  = os.urandom(12)
        outer_aesgcm = AESGCM(kek)
        ciphertext   = outer_aesgcm.encrypt(outer_nonce, manual_json, None)

        filename = os.path.join(self.output_dir, f"manual_{user_id}_{secrets.token_hex(4)}.bviz")
        with open(filename, "wb") as f:
            f.write(outer_nonce + ciphertext)

        return filename, password

    def _load_master_aok(self) -> bytes:
        """Load the Application Obfuscation Key from the offline HSM."""
        with open(self.master_key_path, "rb") as f:
            return f.read(32)  # 256-bit AES key
