import random
import smtplib
import os
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from email.mime.text import MIMEText
from .db import get_db

EMAIL    = os.getenv("EMAIL")
PASSWORD = os.getenv("PASSWORD")

_executor   = ThreadPoolExecutor(max_workers=4)

_smtp_lock = threading.Lock()
_smtp_conn = None


def generate_otp() -> str:
    return str(random.randint(100000, 999999))


def _get_smtp() -> smtplib.SMTP:
    """Return a live, authenticated SMTP connection — reconnect if stale."""
    global _smtp_conn
    # Try to reuse existing connection
    try:
        if _smtp_conn:
            _smtp_conn.noop()   # ping — raises if the connection is dead
            return _smtp_conn
    except Exception:
        _smtp_conn = None

    # Build a fresh connection
    server = smtplib.SMTP("smtp.gmail.com", 587, timeout=10)
    server.ehlo()
    server.starttls()
    server.ehlo()
    server.login(EMAIL, PASSWORD)
    _smtp_conn = server
    print("✅ New SMTP connection established")
    return server


def send_otp_email(to_email: str, otp: str) -> bool:
   
    if not EMAIL or not PASSWORD:
        print("❌ EMAIL or PASSWORD env vars are not set — cannot send OTP.")
        return False

    msg = MIMEText(
        f"Your InclusiveBridge verification code is: {otp}\n\n"
        f"This code expires in 5 minutes. Do not share it with anyone."
    )
    msg["Subject"] = "InclusiveBridge — Your OTP Code"
    msg["From"]    = EMAIL
    msg["To"]      = to_email

    with _smtp_lock:
        # First attempt
        try:
            server = _get_smtp()
            server.send_message(msg)
            print(f"✅ OTP sent to {to_email}")
            return True
        except Exception as e:
            print(f"⚠️  Email error (will retry once): {e}")
            global _smtp_conn
            _smtp_conn = None   # force reconnect on retry

        # Retry once with a fresh connection
        try:
            server = _get_smtp()
            server.send_message(msg)
            print(f"✅ OTP sent to {to_email} (retry succeeded)")
            return True
        except Exception as e:
            print(f"❌ Email failed after retry: {e}")
            _smtp_conn = None
            return False


async def send_otp_email_async(to_email: str, otp: str) -> bool:
   
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(_executor, send_otp_email, to_email, otp)


def store_otp(email: str, otp: str) -> None:
    """Delete any existing OTP for this email and store the new one."""
    with get_db() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM otps WHERE email = %s", (email,))
        c.execute("INSERT INTO otps (email, otp) VALUES (%s, %s)", (email, otp))


def verify_otp(email: str, otp: str) -> bool:
   
    with get_db() as conn:
        c = conn.cursor()
        c.execute(
            """
            SELECT otp FROM otps
            WHERE email = %s
              AND created_at > NOW() - INTERVAL '5 minutes'
            ORDER BY id DESC
            LIMIT 1
            """,
            (email,),
        )
        row = c.fetchone()
        if not row:
            return False
        is_valid = row[0] == otp
        if is_valid:
            c.execute("DELETE FROM otps WHERE email = %s", (email,))
        return is_valid