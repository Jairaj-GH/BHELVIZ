"""
BHELVIZ — Development SQLite Database (replaces Oracle 19c in production)
════════════════════════════════════════════════════════════════════════════
In production, all DB access goes through Oracle 19c with TDE + AES-256-GCM.
This module provides a dev/demo mode using SQLite so the app runs without Oracle.

IMPORTANT: This is a DEVELOPMENT ONLY module.
  - Uses SQLite instead of Oracle.
  - Stores mock plaintext data (encrypted columns contain fake ciphertext strings).
  - No real TDE, no real Vault, no real AES-256-GCM encryption.
  - Never deploy this in production.
"""
from __future__ import annotations

import json
import os
import base64
from datetime import datetime, date

from sqlalchemy import (
    create_engine, MetaData, Table, Column,
    Integer, String, DateTime, Date, Float, Text,
    ForeignKey, inspect, text
)
from sqlalchemy.orm import sessionmaker, declarative_base

Base = declarative_base()

# ── CONFIG ────────────────────────────────────────────────────────────────────

DEV_DB_PATH = os.environ.get("BHELVIZ_DEV_DB", "bhelviz_dev.db")
DEV_MODE    = os.environ.get("BHELVIZ_DEV_MODE", "true").lower() == "true"

# ── ORM MODELS (dev SQLite only) ──────────────────────────────────────────────

class DepartmentOrm(Base):
    __tablename__ = "department"
    department_id        = Column(Integer, primary_key=True, autoincrement=True)
    dept_code            = Column(String(30), unique=True, nullable=False)
    dept_name_enc        = Column(String(512), nullable=False)  # "encrypted" mock
    parent_department_id = Column(Integer, ForeignKey("department.department_id"), nullable=True)
    created_at           = Column(DateTime, default=datetime.utcnow)


class RoleLuOrm(Base):
    __tablename__ = "role_lu"
    role_code = Column(String(30), primary_key=True)
    role_name = Column(String(100), unique=True, nullable=False)


class ShiftOrm(Base):
    __tablename__ = "shift"
    shift_id   = Column(Integer, primary_key=True, autoincrement=True)
    shift_code = Column(String(30), unique=True, nullable=False)
    shift_name = Column(String(100), nullable=False)
    start_time = Column(String(8))
    end_time   = Column(String(8))


class AttendanceStatusLuOrm(Base):
    __tablename__ = "status_lu"
    status_code = Column(String(30), primary_key=True)
    status_name            = Column(String(100), nullable=False)


class EmployeeOrm(Base):
    __tablename__ = "employee"
    employee_id           = Column(Integer, primary_key=True, autoincrement=True)
    employee_no_enc       = Column(String(512), unique=True, nullable=False)
    full_name_enc         = Column(String(1024), nullable=False)
    department_id         = Column(Integer, ForeignKey("department.department_id"), nullable=False)
    current_role_code     = Column(String(30), ForeignKey("role_lu.role_code"), nullable=False)
    employment_status_enc = Column(String(512), nullable=False)
    hired_at_enc          = Column(String(256), nullable=False)
    active_flag           = Column(String(1), default="Y", nullable=False)


class AttendanceOrm(Base):
    __tablename__ = "attendance"
    attendance_id          = Column(Integer, primary_key=True, autoincrement=True)
    employee_id            = Column(Integer, ForeignKey("employee.employee_id"), nullable=False)
    shift_id               = Column(Integer, ForeignKey("shift.shift_id"), nullable=False)
    att_date          = Column(DateTime, nullable=False)
    status_code = Column(String(30), ForeignKey("status_lu.status_code"), nullable=False)
    attendance_penalty     = Column(Float, nullable=True)
    source_channel         = Column(String(30), default="BIOMETRIC")
    device_id              = Column(String(50), nullable=True)
    created_at             = Column(DateTime, default=datetime.utcnow)


class AccessRequestOrm(Base):
    __tablename__ = "access_request"
    id             = Column(Integer, primary_key=True, autoincrement=True)
    full_name      = Column(String(200), nullable=False)
    email          = Column(String(200), nullable=False)
    department     = Column(String(100), nullable=False)
    justification  = Column(Text, nullable=False)
    status         = Column(String(20), default="pending")
    created_at     = Column(DateTime, default=datetime.utcnow)
    reviewed_at    = Column(DateTime, nullable=True)
    reviewed_by    = Column(String(200), nullable=True)
    audit_id       = Column(String(50), nullable=False)


class AuditLogOrm(Base):
    __tablename__ = "audit_log"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    timestamp  = Column(DateTime, default=datetime.utcnow)
    action     = Column(String(100), nullable=False)
    actor      = Column(String(200), nullable=False)
    detail     = Column(Text, nullable=False)
    level      = Column(String(10), default="INFO")
    session_id = Column(String(64), nullable=True)
    ip_hash    = Column(String(32), nullable=True)


# ── FAKE ENCRYPTION HELPERS ───────────────────────────────────────────────────

def fake_enc(value: str) -> str:
    """Simulate AES-256-GCM ciphertext for demo data. NOT real encryption."""
    encoded = base64.b64encode(value.encode()).decode()
    return f"AES-GCM::{encoded[:20]}…"


# ── SEED DATA ─────────────────────────────────────────────────────────────────

DEPARTMENTS = [
    ("POWER_SYSTEMS",  "Power Systems Division"),
    ("TRANSMISSION",   "Transmission & Distribution"),
    ("MANUFACTURING",  "Heavy Manufacturing"),
    ("HR",             "Human Resources"),
    ("SECURITY",       "Plant Security"),
    ("R&D",            "Research & Development"),
    ("BOILER_DIV",     "Boiler Division"),
]

ROLES = [
    ("EXECUTIVE",   "Executive"),
    ("SUPERVISOR",  "Supervisor"),
    ("WORKMAN",     "Workman"),
]

SHIFTS = [
    ("MORNING",    "Morning Shift",   "06:00", "14:00"),
    ("AFTERNOON",  "Afternoon Shift", "14:00", "22:00"),
    ("NIGHT",      "Night Shift",     "22:00", "06:00"),
]

STATUSES = [
    ("PRESENT",       "Present"),
    ("ABSENT",        "Absent"),
    ("LATE",          "Late"),
    ("FALSE_PRESENT", "False / Proxy Attendance"),
]

FIRST_NAMES = ["Arjun","Priya","Vikram","Anita","Ravi","Sunita","Mohan","Lata","Sanjay","Deepa",
               "Kiran","Rajesh","Meena","Suresh","Kavya","Anil","Pooja","Ramesh","Seema","Vinod"]
LAST_NAMES  = ["Sharma","Kumar","Patel","Singh","Nair","Reddy","Rao","Mehta","Joshi","Iyer",
               "Gupta","Verma","Das","Shah","Pillai","Bose","Chatterjee","Mishra","Tiwari","Dubey"]


def seed_database(session) -> None:
    """Populate dev database with 200 realistic mock employees + 7 days attendance."""
    import random
    from datetime import timedelta
    random.seed(42)

    # Departments
    dept_ids = {}
    for i, (code, name) in enumerate(DEPARTMENTS):
        d = DepartmentOrm(dept_code=code, dept_name_enc=fake_enc(name))
        session.add(d)
        session.flush()
        dept_ids[code] = d.department_id

    # Roles
    for code, name in ROLES:
        session.add(RoleLuOrm(role_code=code, role_name=name))

    # Shifts
    shift_ids = {}
    for code, name, st, et in SHIFTS:
        s = ShiftOrm(shift_code=code, shift_name=name, start_time=st, end_time=et)
        session.add(s)
        session.flush()
        shift_ids[code] = s.shift_id

    # Status LU
    for code, name in STATUSES:
        session.add(AttendanceStatusLuOrm(status_code=code, status_name=name))

    session.flush()

    # Employees (200)
    emp_ids = []
    dept_codes = list(dept_ids.keys())
    for i in range(200):
        fn = FIRST_NAMES[i % len(FIRST_NAMES)]
        ln = LAST_NAMES[(i * 3) % len(LAST_NAMES)]
        full_name  = f"{fn} {ln}"
        emp_no     = f"BHEL{str(i + 1).zfill(5)}"
        role_code  = "EXECUTIVE" if i % 20 == 0 else ("SUPERVISOR" if i % 5 == 0 else "WORKMAN")
        dept_code  = dept_codes[i % len(dept_codes)]
        hire_year  = 2006 + (i % 18)
        hire_month = (i % 12) + 1

        e = EmployeeOrm(
            employee_no_enc       = fake_enc(emp_no),
            full_name_enc         = fake_enc(full_name),
            department_id         = dept_ids[dept_code],
            current_role_code     = role_code,
            employment_status_enc = fake_enc("ACTIVE"),
            hired_at_enc          = fake_enc(f"{hire_year}-{hire_month:02d}-01"),
            active_flag           = "Y",
        )
        session.add(e)
        session.flush()
        emp_ids.append((e.employee_id, i))

    # 7 days of attendance records
    today = datetime.utcnow().date()
    for day_offset in range(7):
        att_date = today - timedelta(days=day_offset)
        for emp_id, idx in emp_ids:
            shift_code = list(shift_ids.keys())[idx % 3]
            r = random.random()
            if r < 0.70:
                status = "PRESENT"
                penalty = None
            elif r < 0.82:
                status = "LATE"
                penalty = -0.5
            elif r < 0.94:
                status = "ABSENT"
                penalty = -1.0
            else:
                status = "FALSE_PRESENT"
                penalty = -1.0

            att_ts = datetime.combine(att_date, datetime.min.time()).replace(
                hour=6 if shift_code == "MORNING" else (14 if shift_code == "AFTERNOON" else 22)
            )
            session.add(AttendanceOrm(
                employee_id            = emp_id,
                shift_id               = shift_ids[shift_code],
                att_date          = att_ts,
                status_code = status,
                attendance_penalty     = penalty,
                source_channel         = "BIOMETRIC",
            ))

    # Seed some audit events
    for evt in [
        ("SYSTEM_INIT",   "SYSTEM",           "BHELVIZ v2.0 started. All security controls active.",      "INFO"),
        ("TDE_VERIFIED",  "SYSTEM",            "Oracle TDE: ACTIVE (dev: SQLite mock).",                   "INFO"),
        ("DB_VAULT_INIT", "SYSTEM",            "Database Vault realms loaded. DBA bypass paths: BLOCKED.", "INFO"),
    ]:
        session.add(AuditLogOrm(action=evt[0], actor=evt[1], detail=evt[2], level=evt[3]))

    session.commit()


# ── ENGINE FACTORY ─────────────────────────────────────────────────────────────

_dev_engine = None
_dev_session_factory = None


def get_dev_engine():
    global _dev_engine, _dev_session_factory
    if _dev_engine is None:
        _dev_engine = create_engine(
            f"sqlite:///{DEV_DB_PATH}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        Base.metadata.create_all(_dev_engine)
        _dev_session_factory = sessionmaker(bind=_dev_engine)

        # Seed if empty
        with _dev_session_factory() as session:
            if session.query(EmployeeOrm).count() == 0:
                seed_database(session)

    return _dev_engine


def get_dev_session():
    engine = get_dev_engine()
    Session = sessionmaker(bind=engine)
    return Session()


# ── ORACLE PRODUCTION CONNECTION ────────────────────────────────────────────

DB_USER = os.getenv("DB_USER")
DB_PASSWORD = os.getenv("DB_PASSWORD")
DB_HOST = os.getenv("DB_HOST")
DB_PORT = os.getenv("DB_PORT")
DB_SERVICE = os.getenv("DB_SERVICE")

_oracle_engine = None
_oracle_session_factory = None


def get_oracle_engine():
    global _oracle_engine, _oracle_session_factory

    if _oracle_engine is None:

        DATABASE_URL = (
            f"oracle+oracledb://{DB_USER}:{DB_PASSWORD}"
            f"@{DB_HOST}:{DB_PORT}/?service_name={DB_SERVICE}"
        )

        _oracle_engine = create_engine(
            DATABASE_URL,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
            echo=False,
        )

        _oracle_session_factory = sessionmaker(bind=_oracle_engine)

    return _oracle_engine


def get_oracle_session():
    global _oracle_session_factory

    if _oracle_session_factory is None:
        get_oracle_engine()

    return _oracle_session_factory()