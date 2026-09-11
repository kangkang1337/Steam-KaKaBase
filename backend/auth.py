"""Small cookie-session account store for synced favorites."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from .db import now_iso, transaction

SESSION_COOKIE = "steamkb_session"

def _hash(value): return hashlib.sha256(value.encode("utf-8")).hexdigest()
def _password(password, salt): return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 210_000).hex()

def register(username, password):
    username = username.strip()
    if not 3 <= len(username) <= 40 or not username.replace("_", "").replace("-", "").isalnum():
        raise ValueError("用户名需为 3–40 位字母、数字、下划线或连字符")
    if len(password) < 8 or len(password) > 128: raise ValueError("密码需为 8–128 位")
    salt = secrets.token_hex(16)
    try:
        with transaction() as conn:
            conn.execute("INSERT INTO users(username,password_hash,password_salt,created_at) VALUES(?,?,?,?)", (username, _password(password, salt), salt, now_iso()))
    except Exception as exc:
        if "UNIQUE" in str(exc).upper(): raise ValueError("用户名已存在") from exc
        raise

def login(username, password, remember):
    with transaction(rows=True) as conn:
        user = conn.execute("SELECT * FROM users WHERE username=?", (username.strip(),)).fetchone()
        if not user or not secrets.compare_digest(user["password_hash"], _password(password, user["password_salt"])):
            raise ValueError("用户名或密码错误")
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        expires = datetime.now(timezone.utc) + timedelta(days=30 if remember else 1)
        conn.execute("INSERT INTO user_sessions(token_hash,user_id,csrf_token,expires_at,created_at) VALUES(?,?,?,?,?)", (_hash(token), user["id"], csrf, expires.replace(microsecond=0).isoformat(), now_iso()))
    return token, csrf, dict(user), expires

def session(token):
    if not token: return None
    with transaction(rows=True) as conn:
        row = conn.execute("SELECT s.csrf_token,u.id,u.username FROM user_sessions s JOIN users u ON u.id=s.user_id WHERE s.token_hash=? AND s.expires_at>?", (_hash(token), now_iso())).fetchone()
    return dict(row) if row else None

def logout(token):
    if token:
        with transaction() as conn: conn.execute("DELETE FROM user_sessions WHERE token_hash=?", (_hash(token),))

