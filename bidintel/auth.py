"""Authentication and Role-Based Access Control (RBAC) module.
Uses Python standard library only for zero-dependency reliability on Jetson Linux.
"""
import hashlib
import hmac
import os
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

SUPERADMIN_EMAIL = os.getenv("BI_SUPERADMIN_EMAIL", "iamvazu@gmail.com").lower().strip()
SUPERADMIN_DEFAULT_PASS = os.getenv("BI_SUPERADMIN_PASSWORD", "Admin#2026!")
SESSION_DAYS = int(os.getenv("BI_SESSION_DAYS", "14"))


def hash_password(password: str) -> str:
    """Hash password using PBKDF2-HMAC-SHA256 with a cryptographically secure salt."""
    salt = secrets.token_bytes(16)
    iterations = 100_000
    derived = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2:sha256:{iterations}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify password against stored PBKDF2 hash using constant-time comparison."""
    if not stored_hash or not stored_hash.startswith("pbkdf2:sha256:"):
        return False
    try:
        header, salt_hex, hash_hex = stored_hash.split("$")
        iterations = int(header.split(":")[-1])
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(candidate, expected)
    except Exception:
        return False


def sanitize_user(row: Any) -> Optional[Dict[str, Any]]:
    """Convert sqlite3.Row or dict to safe user dict excluding sensitive hashes."""
    if not row:
        return None
    d = dict(row)
    d.pop("password_hash", None)
    return d


def log_audit(conn: sqlite3.Connection, user_id: Optional[str], action: str, details: str = "", ip: str = ""):
    """Record an audit trail event."""
    try:
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO audit_logs (user_id, action, details, ip_address, created_at) VALUES (?,?,?,?,?)",
            (user_id, action, details, ip, now)
        )
        conn.commit()
    except Exception as e:
        print(f"[AUTH AUDIT WARN] Failed to log audit event: {e}")


def seed_default_accounts(conn: sqlite3.Connection):
    """Ensure the superadmin account and starter demo accounts exist."""
    now = datetime.now().isoformat(timespec="seconds")
    
    # 1. Super Admin: iamvazu@gmail.com
    super_row = conn.execute("SELECT * FROM users WHERE email = ?", (SUPERADMIN_EMAIL,)).fetchone()
    if not super_row:
        super_id = f"usr_{uuid.uuid4().hex[:12]}"
        super_hash = hash_password(SUPERADMIN_DEFAULT_PASS)
        conn.execute(
            """INSERT INTO users (id, email, password_hash, first_name, last_name, business_name, role, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'superadmin', 1, ?)""",
            (super_id, SUPERADMIN_EMAIL, super_hash, "Iam", "Vazu", "STRONGERbuilt", now)
        )
        conn.commit()
        print(f"[AUTH] Created Superadmin account: {SUPERADMIN_EMAIL}")
    else:
        # Guarantee role remains superadmin
        if super_row["role"] != "superadmin":
            conn.execute("UPDATE users SET role='superadmin' WHERE id = ?", (super_row["id"],))
            conn.commit()

    # 2. Demo Admin: admin@strongerbuilt.com
    admin_email = "admin@strongerbuilt.com"
    if not conn.execute("SELECT id FROM users WHERE email = ?", (admin_email,)).fetchone():
        conn.execute(
            """INSERT INTO users (id, email, password_hash, first_name, last_name, business_name, role, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'admin', 1, ?)""",
            (f"usr_{uuid.uuid4().hex[:12]}", admin_email, hash_password("Admin#2026!"), "Sarah", "Chen", "STRONGERbuilt Ops", now)
        )
        conn.commit()

    # 3. Demo Employee: employee@strongerbuilt.com
    emp_email = "employee@strongerbuilt.com"
    if not conn.execute("SELECT id FROM users WHERE email = ?", (emp_email,)).fetchone():
        conn.execute(
            """INSERT INTO users (id, email, password_hash, first_name, last_name, business_name, role, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'employee', 1, ?)""",
            (f"usr_{uuid.uuid4().hex[:12]}", emp_email, hash_password("Admin#2026!"), "Marcus", "Vance", "STRONGERbuilt Field", now)
        )
        conn.commit()


def create_user(
    conn: sqlite3.Connection,
    email: str,
    password: str,
    first_name: str,
    last_name: str,
    business_name: str = "",
    role: str = "employee",
    actor_user: Optional[Dict[str, Any]] = None,
    ip: str = ""
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Register a new user."""
    email = email.lower().strip()
    if not email or "@" not in email:
        return None, "Invalid email address"
    if not password or len(password) < 8:
        return None, "Password must be at least 8 characters"
    if not first_name.strip():
        return None, "First name is required"
    if not last_name.strip():
        return None, "Last name is required"

    if role not in ("superadmin", "admin", "employee"):
        role = "employee"

    # Only superadmin can designate superadmin or admin accounts directly
    if role in ("superadmin", "admin"):
        if not actor_user or actor_user.get("role") != "superadmin":
            role = "employee"

    # Check if user already exists
    existing = conn.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
    if existing:
        return None, "An account with this email already exists"

    user_id = f"usr_{uuid.uuid4().hex[:12]}"
    pwd_hash = hash_password(password)
    now = datetime.now().isoformat(timespec="seconds")

    try:
        conn.execute(
            """INSERT INTO users (id, email, password_hash, first_name, last_name, business_name, role, is_active, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)""",
            (user_id, email, pwd_hash, first_name.strip(), last_name.strip(), business_name.strip(), role, now)
        )
        conn.commit()
        log_audit(conn, user_id, "user_created", f"Registered {email} with role {role}", ip)
        created = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return sanitize_user(created), None
    except Exception as e:
        return None, str(e)


def authenticate(
    conn: sqlite3.Connection,
    email: str,
    password: str,
    ip: str = "",
    user_agent: str = ""
) -> Tuple[Optional[Dict[str, Any]], Optional[str], Optional[str]]:
    """Authenticate email + password, create session token."""
    email = email.lower().strip()
    user_row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if not user_row:
        return None, None, "Invalid email or password"

    if not user_row["is_active"]:
        return None, None, "This account has been deactivated. Contact an administrator."

    if not verify_password(password, user_row["password_hash"]):
        log_audit(conn, user_row["id"], "login_failed", f"Failed attempt for {email}", ip)
        return None, None, "Invalid email or password"

    # Create session
    token = secrets.token_hex(32)
    now_dt = datetime.now()
    now = now_dt.isoformat(timespec="seconds")
    expires = (now_dt + timedelta(days=SESSION_DAYS)).isoformat(timespec="seconds")

    conn.execute(
        """INSERT INTO sessions (token, user_id, created_at, expires_at, ip_address, user_agent)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (token, user_row["id"], now, expires, ip, user_agent[:250])
    )
    conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (now, user_row["id"]))
    conn.commit()

    log_audit(conn, user_row["id"], "login_success", f"Logged in from {ip}", ip)
    user_data = sanitize_user(user_row)
    return user_data, token, None


def get_user_from_token(conn: sqlite3.Connection, token: str) -> Optional[Dict[str, Any]]:
    """Validate session token and return user."""
    if not token or len(token) < 20:
        return None

    now = datetime.now().isoformat(timespec="seconds")
    row = conn.execute(
        """SELECT u.*, s.expires_at FROM sessions s
           JOIN users u ON s.user_id = u.id
           WHERE s.token = ?""",
        (token,)
    ).fetchone()

    if not row:
        return None

    if row["expires_at"] < now:
        # Expired session
        conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
        conn.commit()
        return None

    if not row["is_active"]:
        return None

    return sanitize_user(row)


def logout(conn: sqlite3.Connection, token: str) -> bool:
    """Invalidate session token."""
    if not token:
        return True
    conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
    conn.commit()
    return True


def list_users(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    """List all registered users without password hashes."""
    rows = conn.execute(
        """SELECT id, email, first_name, last_name, business_name, role, is_active, created_at, last_login
           FROM users ORDER BY created_at DESC"""
    ).fetchall()
    return [dict(r) for r in rows]


def update_user_role(
    conn: sqlite3.Connection,
    user_id: str,
    new_role: str,
    actor_user: Dict[str, Any],
    ip: str = ""
) -> Tuple[bool, Optional[str]]:
    """Update user role (restricted to superadmin)."""
    if actor_user.get("role") != "superadmin":
        return False, "Permission denied: Only Superadmin can change user roles"

    if new_role not in ("superadmin", "admin", "employee"):
        return False, "Invalid role"

    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return False, "User not found"

    # Protect superadmin account from demoting itself if it's the primary one
    if target["email"] == SUPERADMIN_EMAIL and new_role != "superadmin":
        return False, f"The primary superadmin account ({SUPERADMIN_EMAIL}) cannot be demoted"

    conn.execute("UPDATE users SET role = ? WHERE id = ?", (new_role, user_id))
    conn.commit()
    log_audit(conn, actor_user.get("id"), "role_updated", f"Changed {target['email']} to {new_role}", ip)
    return True, None


def toggle_user_status(
    conn: sqlite3.Connection,
    user_id: str,
    is_active: bool,
    actor_user: Dict[str, Any],
    ip: str = ""
) -> Tuple[bool, Optional[str]]:
    """Enable or disable user account (restricted to admin and superadmin)."""
    actor_role = actor_user.get("role")
    if actor_role not in ("superadmin", "admin"):
        return False, "Permission denied"

    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return False, "User not found"

    if target["role"] == "superadmin" and actor_role != "superadmin":
        return False, "Permission denied: Admins cannot alter superadmin status"

    if target["email"] == SUPERADMIN_EMAIL and not is_active:
        return False, "Primary superadmin account cannot be deactivated"

    conn.execute("UPDATE users SET is_active = ? WHERE id = ?", (1 if is_active else 0, user_id))
    if not is_active:
        # Invalidate existing sessions
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    log_audit(conn, actor_user.get("id"), "status_toggled", f"{target['email']} active={is_active}", ip)
    return True, None


def delete_user(
    conn: sqlite3.Connection,
    user_id: str,
    actor_user: Dict[str, Any],
    ip: str = ""
) -> Tuple[bool, Optional[str]]:
    """Delete a user account (restricted to superadmin)."""
    if actor_user.get("role") != "superadmin":
        return False, "Permission denied: Only Superadmin can delete accounts"

    target = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not target:
        return False, "User not found"

    if target["email"] == SUPERADMIN_EMAIL:
        return False, "Primary superadmin account cannot be deleted"

    conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    conn.commit()
    log_audit(conn, actor_user.get("id"), "user_deleted", f"Deleted {target['email']}", ip)
    return True, None


def change_password(
    conn: sqlite3.Connection,
    user_id: str,
    old_pass: str,
    new_pass: str,
    ip: str = ""
) -> Tuple[bool, Optional[str]]:
    """Update user password verifying old password first."""
    if not new_pass or len(new_pass) < 8:
        return False, "New password must be at least 8 characters"

    user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    if not user:
        return False, "User not found"

    if not verify_password(old_pass, user["password_hash"]):
        return False, "Current password does not match"

    new_hash = hash_password(new_pass)
    conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (new_hash, user_id))
    conn.commit()
    log_audit(conn, user_id, "password_changed", "User updated their password", ip)
    return True, None


def get_system_diagnostics(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Gather diagnostic metrics for the Superadmin dashboard."""
    users_by_role = {}
    for r in conn.execute("SELECT role, COUNT(*) as cnt FROM users GROUP BY role").fetchall():
        users_by_role[r["role"]] = r["cnt"]

    active_sessions = conn.execute("SELECT COUNT(*) FROM sessions WHERE expires_at > datetime('now')").fetchone()[0]
    total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # Database table metrics
    table_counts = {}
    for tbl in ("events", "attachments", "chunks", "unspsc", "facts", "analysis", "briefs", "firms"):
        try:
            cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            table_counts[tbl] = cnt
        except Exception:
            table_counts[tbl] = 0

    # Recent audit logs
    recent_audits = [
        dict(r) for r in conn.execute(
            """SELECT a.id, a.action, a.details, a.ip_address, a.created_at, u.email, u.role
               FROM audit_logs a LEFT JOIN users u ON a.user_id = u.id
               ORDER BY a.created_at DESC LIMIT 20"""
        ).fetchall()
    ]

    return {
        "users": {
            "total": total_users,
            "by_role": users_by_role,
            "active_sessions": active_sessions
        },
        "database": table_counts,
        "recent_audit": recent_audits
    }
