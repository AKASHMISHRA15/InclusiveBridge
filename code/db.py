import psycopg2
import psycopg2.extras
import os
import hashlib
import hmac
import json
from dotenv import load_dotenv
import pathlib
from contextlib import contextmanager

ENV_PATH = pathlib.Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

DATABASE_URL = os.getenv("DATABASE_URL")


@contextmanager
def get_db():
    """Use this everywhere — automatically commits and closes."""
    conn = psycopg2.connect(DATABASE_URL)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_conn():
    return psycopg2.connect(DATABASE_URL)


# ── Password hashing (no external deps, uses stdlib) ─────────────────────────

def hash_password(password: str) -> str:
    """Return a salted SHA-256 hex digest of the password."""
    salt = os.urandom(16).hex()          # 32-char hex salt
    digest = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return f"{salt}:{digest}"


def check_password(password: str, stored: str) -> bool:
    """Constant-time comparison to prevent timing attacks."""
    try:
        salt, digest = stored.split(":", 1)
    except ValueError:
        # Legacy: stored value has no salt → plain-text era, reject safely
        return False
    expected = hashlib.sha256(f"{salt}:{password}".encode()).hexdigest()
    return hmac.compare_digest(expected, digest)


# ── Schema init ───────────────────────────────────────────────────────────────

def init_db():
    with get_db() as conn:
        c = conn.cursor()
        c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            SERIAL PRIMARY KEY,
            name          TEXT,
            age           INTEGER,
            gender        TEXT,
            email         TEXT UNIQUE,
            password      TEXT,
            phone         TEXT,
            role          TEXT,
            caregiver_id  TEXT,
            job_position  TEXT
        )""")
        c.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS job_position TEXT")
        c.execute("""
        CREATE TABLE IF NOT EXISTS logs (
            id         SERIAL PRIMARY KEY,
            session_id TEXT,
            sender     TEXT,
            message    TEXT,
            type       TEXT,
            timestamp  TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS otps (
            id         SERIAL PRIMARY KEY,
            email      TEXT,
            otp        TEXT,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id           SERIAL PRIMARY KEY,
            session_id   TEXT UNIQUE,
            sp_id        TEXT,
            owner_user_id INTEGER,
            patient_name TEXT,
            age          INTEGER,
            gender       TEXT,
            disease      TEXT,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            active       INTEGER DEFAULT 1
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS session_dashboards (
            id                  SERIAL PRIMARY KEY,
            session_id          TEXT,
            user_id             INTEGER,
            session_name        TEXT,
            patient_name        TEXT,
            age                 INTEGER,
            gender              TEXT,
            disease             TEXT,
            session_created_at  TIMESTAMPTZ,
            session_ended_at    TIMESTAMPTZ DEFAULT NOW(),
            total_messages      INTEGER DEFAULT 0,
            total_alerts        INTEGER DEFAULT 0,
            top_alerts          JSONB DEFAULT '[]'::jsonb,
            created_at          TIMESTAMPTZ DEFAULT NOW(),
            UNIQUE (session_id, user_id)
        )""")
        c.execute("ALTER TABLE sessions ADD COLUMN IF NOT EXISTS owner_user_id INTEGER")
        c.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'session_dashboards_session_id_key'
                  AND conrelid = 'session_dashboards'::regclass
            ) THEN
                ALTER TABLE session_dashboards DROP CONSTRAINT session_dashboards_session_id_key;
            END IF;

            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'session_dashboards_session_id_user_id_key'
                  AND conrelid = 'session_dashboards'::regclass
            ) THEN
                ALTER TABLE session_dashboards
                ADD CONSTRAINT session_dashboards_session_id_user_id_key
                UNIQUE (session_id, user_id);
            END IF;
        END $$;
        """)
        c.execute("""
        CREATE TABLE IF NOT EXISTS voice_files (
            file_id    TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            mime_type  TEXT NOT NULL DEFAULT 'audio/webm',
            data       BYTEA NOT NULL,
            created_at TIMESTAMPTZ DEFAULT NOW()
        )""")
        c.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id           SERIAL PRIMARY KEY,
            session_id   TEXT NOT NULL,
            role         TEXT NOT NULL,
            endpoint     TEXT NOT NULL UNIQUE,
            subscription JSONB NOT NULL,
            created_at   TIMESTAMPTZ DEFAULT NOW(),
            updated_at   TIMESTAMPTZ DEFAULT NOW()
        )""")
    print("✅ Database initialized (Supabase PostgreSQL)")


# ── CRUD helpers ──────────────────────────────────────────────────────────────

def save_log(session_id, sender, message, type_):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO logs (session_id, sender, message, type) VALUES (%s, %s, %s, %s)",
            (session_id, sender, message, type_),
        )


def save_session(session_id, sp_id, name, age, gender, disease, owner_user_id=None):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO sessions (session_id, sp_id, owner_user_id, patient_name, age, gender, disease)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (session_id) DO UPDATE SET
                sp_id        = EXCLUDED.sp_id,
                owner_user_id = EXCLUDED.owner_user_id,
                patient_name = EXCLUDED.patient_name,
                age          = EXCLUDED.age,
                gender       = EXCLUDED.gender,
                disease      = EXCLUDED.disease,
                active       = 1
            """,
            (session_id, sp_id, owner_user_id, name, age, gender, disease),
        )


def find_session_by_id(session_id):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM sessions WHERE session_id = %s AND active = 1", (session_id,))
        row = c.fetchone()
        return dict(row) if row else None


def find_sessions_by_patient(name, age=None, gender=None):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query  = "SELECT * FROM sessions WHERE patient_name ILIKE %s AND active = 1"
        params = [f"%{name}%"]
        if age:
            query  += " AND age = %s"
            params.append(age)
        if gender:
            query  += " AND gender = %s"
            params.append(gender)
        c.execute(query, params)
        return [dict(r) for r in c.fetchall()]


def verify_sp_id(session_id, sp_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT id FROM sessions WHERE session_id = %s AND sp_id = %s AND active = 1",
            (session_id, sp_id),
        )
        return c.fetchone() is not None


def end_session_db(session_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE sessions SET active = 0 WHERE session_id = %s", (session_id,))


def create_session_dashboard(session_id, user_id=None):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute("SELECT * FROM sessions WHERE session_id = %s", (session_id,))
        session = c.fetchone()
        if not session:
            return None

        c.execute("SELECT sender, message, type FROM logs WHERE session_id = %s", (session_id,))
        logs = c.fetchall()
        total_messages = len(logs)
        alert_counts = {}
        for log in logs:
            sender = (log.get("sender") or "").lower()
            msg_type = (log.get("type") or "").lower()
            message = (log.get("message") or "").strip()
            if sender == "system" or msg_type == "alert":
                label = message.split(" for ", 1)[0].strip()
                label = label[:80] if label else "System alert"
                alert_counts[label] = alert_counts.get(label, 0) + 1

        top_alerts = [
            {"type": alert_type, "count": count}
            for alert_type, count in sorted(alert_counts.items(), key=lambda item: item[1], reverse=True)[:3]
        ]

        c.execute(
            """
            INSERT INTO session_dashboards (
                session_id, user_id, session_name, patient_name, age, gender, disease,
                session_created_at, session_ended_at, total_messages, total_alerts, top_alerts
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s::jsonb)
            ON CONFLICT (session_id, user_id) DO UPDATE SET
                user_id = COALESCE(EXCLUDED.user_id, session_dashboards.user_id),
                session_name = EXCLUDED.session_name,
                patient_name = EXCLUDED.patient_name,
                age = EXCLUDED.age,
                gender = EXCLUDED.gender,
                disease = EXCLUDED.disease,
                session_created_at = EXCLUDED.session_created_at,
                session_ended_at = NOW(),
                total_messages = EXCLUDED.total_messages,
                total_alerts = EXCLUDED.total_alerts,
                top_alerts = EXCLUDED.top_alerts
            RETURNING *
            """,
            (
                session_id,
                user_id,
                session["session_id"],
                session["patient_name"],
                session["age"],
                session["gender"],
                session["disease"],
                session["created_at"],
                total_messages,
                sum(alert_counts.values()),
                json.dumps(top_alerts),
            ),
        )
        row = c.fetchone()
        return dict(row) if row else None


def get_user_profile(user_id):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            "SELECT id, name, age, gender, email, phone, role, caregiver_id, job_position FROM users WHERE id = %s",
            (user_id,),
        )
        row = c.fetchone()
        return dict(row) if row else None


def update_user_profile(user_id, age=None, job_position=None, phone=None):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            """
            UPDATE users
            SET age = COALESCE(%s, age),
                job_position = COALESCE(%s, job_position),
                phone = COALESCE(%s, phone)
            WHERE id = %s
            RETURNING id, name, age, gender, email, phone, role, caregiver_id, job_position
            """,
            (age, job_position, phone, user_id),
        )
        row = c.fetchone()
        return dict(row) if row else None


def get_dashboards_for_user(user_id):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            """
            SELECT *
            FROM session_dashboards
            WHERE user_id = %s
            ORDER BY session_ended_at DESC NULLS LAST, created_at DESC
            """,
            (user_id,),
        )
        return [dict(r) for r in c.fetchall()]


def get_session_dashboard(session_id, user_id=None):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        if user_id is None:
            c.execute("SELECT * FROM session_dashboards WHERE session_id = %s", (session_id,))
        else:
            c.execute(
                "SELECT * FROM session_dashboards WHERE session_id = %s AND user_id = %s",
                (session_id, user_id),
            )
        row = c.fetchone()
        return dict(row) if row else None


def get_chat_by_session(session_id):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            "SELECT id, sender, message, type, timestamp, COALESCE(is_read, 0) AS is_read FROM logs WHERE session_id = %s ORDER BY id ASC",
            (session_id,),
        )
        return [dict(r) for r in c.fetchall()]


def mark_messages_read(session_id, reader):
    """Mark all messages NOT sent by `reader` as read (is_read = 1).
    reader is 'Patient' or 'Caregiver' depending on who is viewing the chat.
    """
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "UPDATE logs SET is_read = 1 WHERE session_id = %s AND sender != %s AND COALESCE(is_read, 0) = 0",
            (session_id, reader),
        )

def save_voice_file(file_id, session_id, mime_type, data):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO voice_files (file_id, session_id, mime_type, data) VALUES (%s, %s, %s, %s)",
            (file_id, session_id, mime_type, psycopg2.Binary(data)),
        )


def get_voice_file_db(file_id):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT mime_type, data FROM voice_files WHERE file_id = %s", (file_id,))
        row = c.fetchone()
        if not row:
            return None
        return {"mime_type": row[0], "data": bytes(row[1])}


def save_push_subscription(session_id, role, subscription):
    endpoint = subscription.get("endpoint")
    if not endpoint:
        raise ValueError("Push subscription missing endpoint")
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO push_subscriptions (session_id, role, endpoint, subscription)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (endpoint) DO UPDATE SET
                session_id = EXCLUDED.session_id,
                role = EXCLUDED.role,
                subscription = EXCLUDED.subscription,
                updated_at = NOW()
            """,
            (session_id, role, endpoint, json.dumps(subscription)),
        )


def get_push_subscriptions(session_id, role):
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            """
            SELECT subscription
            FROM push_subscriptions
            WHERE session_id = %s AND role = %s
            ORDER BY updated_at DESC
            """,
            (session_id, role),
        )
        return [dict(r["subscription"]) for r in c.fetchall()]


def count_push_subscriptions(session_id, role):
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT COUNT(*) FROM push_subscriptions WHERE session_id = %s AND role = %s",
            (session_id, role),
        )
        return c.fetchone()[0]


def delete_push_subscription(endpoint):
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM push_subscriptions WHERE endpoint = %s", (endpoint,))
