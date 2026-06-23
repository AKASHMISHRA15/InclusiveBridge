from dotenv import load_dotenv
import pathlib, os, webbrowser, json, time, random, uuid, mimetypes, base64, re

# Fix for silent MediaPipe hangs on cloud deployments (Railway/Linux):
# Explicitly register WASM MIME type so browsers don't reject instantiateStreaming
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/manifest+json", ".webmanifest")
import psycopg2, psycopg2.extras
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, Form
from fastapi.responses import FileResponse, RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
import uvicorn
from .auth import generate_otp, send_otp_email_async, store_otp, verify_otp
from .db import (
    save_log, init_db, save_session, find_session_by_id,
    find_sessions_by_patient, verify_sp_id, end_session_db, get_db,
    hash_password, check_password, save_voice_file, get_voice_file_db,
    save_push_subscription, get_push_subscriptions, count_push_subscriptions,
    delete_push_subscription, create_session_dashboard, get_user_profile,
    update_user_profile, get_dashboards_for_user, get_session_dashboard,
)
try:
    from pywebpush import webpush, WebPushException
except ImportError:
    webpush = None

ENV_PATH = pathlib.Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=ENV_PATH)

# ── Environment ───────────────────────────────────────────────────────────────

IS_PRODUCTION = os.getenv("ENVIRONMENT", "development").lower() == "production"

SESSION_SECRET = os.getenv("SESSION_SECRET")
if not SESSION_SECRET:
    if IS_PRODUCTION:
        raise RuntimeError("SESSION_SECRET environment variable must be set in production.")
    SESSION_SECRET = "local-dev-secret-do-not-use-in-prod"
    print("⚠️  SESSION_SECRET not set — using insecure default (dev only).")

# ── App setup ─────────────────────────────────────────────────────────────────

app = FastAPI(title="InclusiveBridge Backend")
app.add_middleware(
    SessionMiddleware,
    secret_key     = SESSION_SECRET,
    session_cookie = "pb_session",
    max_age        = 28800,
    https_only     = IS_PRODUCTION,
    same_site      = "lax",
)

BASE_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FRONTEND_PATH = os.path.join(BASE_DIR, "frontend")
VOICE_DIR     = os.path.join(BASE_DIR, "data", "voice")

# ── Web Push Notifications ────────────────────────────────────────────────────
VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "").strip()
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "").strip()
VAPID_CLAIMS = {"sub": "mailto:admin@inclusivebridge.local"}
push_subscriptions = {} # Key: (session_id, role), Value: push_subscription_json_dict

def _load_vapid_file():
    paths = (
        os.path.join(BASE_DIR, "vapid.json"),
        os.path.join(os.path.dirname(__file__), "vapid.json"),
    )
    for vapid_path in paths:
        if not os.path.isfile(vapid_path):
            continue
        try:
            with open(vapid_path, "r", encoding="utf-8-sig") as f:
                return json.load(f), vapid_path
        except UnicodeError:
            with open(vapid_path, "r", encoding="utf-16") as f:
                return json.load(f), vapid_path
    return {}, None

if not (VAPID_PRIVATE_KEY and VAPID_PUBLIC_KEY):
    v, loaded_vapid_path = _load_vapid_file()
    VAPID_PRIVATE_KEY = VAPID_PRIVATE_KEY or (v.get("private") or "").strip()
    VAPID_PUBLIC_KEY = VAPID_PUBLIC_KEY or (v.get("public") or "").strip()
    if loaded_vapid_path:
        print(f"Web Push VAPID keys loaded from {loaded_vapid_path}")
    else:
        print("VAPID config not found. Web Push disabled.")

def _decode_base64url(value: str) -> bytes:
    padded = value + "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode(padded.encode("ascii"))

def get_vapid_status() -> dict:
    status = {
        "pywebpush_available": webpush is not None,
        "private_key_configured": bool(VAPID_PRIVATE_KEY),
        "public_key_configured": bool(VAPID_PUBLIC_KEY),
        "public_key_valid_shape": False,
        "issues": [],
    }
    if webpush is None:
        status["issues"].append("pywebpush is not installed or failed to import")
    if not VAPID_PRIVATE_KEY:
        status["issues"].append("VAPID private key is missing")
    if not VAPID_PUBLIC_KEY:
        status["issues"].append("VAPID public key is missing")
    else:
        try:
            raw = _decode_base64url(VAPID_PUBLIC_KEY)
            status["public_key_bytes"] = len(raw)
            status["public_key_valid_shape"] = len(raw) == 65 and raw[0] == 4
            if not status["public_key_valid_shape"]:
                status["issues"].append("VAPID public key is not an uncompressed P-256 public key")
        except Exception as exc:
            status["issues"].append(f"VAPID public key is not valid base64url: {exc}")
    status["ready"] = (
        status["pywebpush_available"]
        and status["private_key_configured"]
        and status["public_key_configured"]
        and status["public_key_valid_shape"]
    )
    return status

def send_web_push(session_id: str, role: str, title: str, body: str):
    if not webpush or not VAPID_PRIVATE_KEY: return
    try:
        db_subs = get_push_subscriptions(session_id, role)
    except Exception as exc:
        print(f"âš ï¸ Push DB lookup failed: {exc}")
        db_subs = []
    if db_subs:
        for sub in db_subs:
            endpoint = sub.get("endpoint", "")
            try:
                webpush(
                    subscription_info=sub,
                    data=json.dumps({"title": title, "body": body}),
                    vapid_private_key=VAPID_PRIVATE_KEY,
                    vapid_claims=VAPID_CLAIMS
                )
            except WebPushException as ex:
                print(f"âš ï¸ Web Push failed: {ex}")
                if endpoint and ex.response is not None and ex.response.status_code in (404, 410):
                    try: delete_push_subscription(endpoint)
                    except Exception: pass
        return
    sub = push_subscriptions.get((session_id, role))
    if not sub: return
    try:
        webpush(
            subscription_info=sub,
            data=json.dumps({"title": title, "body": body}),
            vapid_private_key=VAPID_PRIVATE_KEY,
            vapid_claims=VAPID_CLAIMS
        )
    except WebPushException as ex:
        print(f"⚠️ Web Push failed: {ex}")
        # If subscription is dead, remove it
        if ex.response is not None and ex.response.status_code in (404, 410):
            push_subscriptions.pop((session_id, role), None)
os.makedirs(VOICE_DIR, exist_ok=True)

# ── Middleware ────────────────────────────────────────────────────────────────

# WasmHeaderMiddleware removed to disable Cross-Origin isolation.
# This disables SharedArrayBuffer, forcing MediaPipe to fall back to single-threaded 
# WebAssembly, preventing severe hangs during model initialization on mobile Safari/Chrome.


class CachedStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".task", ".wasm")):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        else:
            response.headers["Cache-Control"] = "no-cache"
        return response

app.mount("/static", CachedStaticFiles(directory=FRONTEND_PATH), name="static")

@app.get("/service-worker.js")
def get_service_worker():
    return FileResponse(os.path.join(FRONTEND_PATH, "service-worker.js"), media_type="application/javascript")
init_db()

# ── Per-session state ─────────────────────────────────────────────────────────

class SessionState:
    def __init__(self):
        self.running              = False
        self.session_active       = True
        self.patient_online       = True
        self.calibrating          = True
        self.calib_start          = None
        self.posture              = "CALIBRATING"
        self.posture_severity     = "NORMAL"
        self.expression_label     = "⏳ Calibrating"
        self.expression_severity  = "NORMAL"

    def to_analysis_msg(self) -> dict:
        return {
            "type":                "analysis",
            "posture":             self.posture,
            "posture_severity":    self.posture_severity,
            "expression_label":    self.expression_label,
            "expression_severity": self.expression_severity,
            "calibrating":         self.calibrating,
            "calib_remaining":     0,
        }

_session_states: dict[str, SessionState] = {}

def get_state(session_id: str) -> SessionState:
    if session_id not in _session_states:
        _session_states[session_id] = SessionState()
    return _session_states[session_id]


def get_active_sid(request: Request):
    """Safely retrieves the session ID based on the user's current role context."""
    sid = request.session.get("monitoring_session")
    if sid: return sid
    return request.session.get("patient_session")

def get_monitoring_sid(request: Request):
    return request.session.get("monitoring_session")

def get_patient_sid(request: Request):
    return request.session.get("patient_session")

# ── WebSocket manager ─────────────────────────────────────────────────────────

class RTCManager:
    def __init__(self):
        self.patients:   dict[str, WebSocket]       = {}
        self.caregivers: dict[str, list[WebSocket]] = {}
        self.patient_hidden: dict[str, bool] = {}
        self.caregiver_hidden: dict[str, dict[WebSocket, bool]] = {}

    async def connect_patient(self, sid: str, ws: WebSocket):
        await ws.accept()
        self.patients[sid] = ws
        self.patient_hidden[sid] = False
        print(f"📡 Patient connected: {sid}")

    async def connect_caregiver(self, sid: str, ws: WebSocket):
        await ws.accept()
        self.caregivers.setdefault(sid, []).append(ws)
        self.caregiver_hidden.setdefault(sid, {})[ws] = False
        print(f"👁  Caregiver connected: {sid}")

    def set_patient_visibility(self, sid: str, hidden: bool):
        self.patient_hidden[sid] = hidden

    def set_caregiver_visibility(self, sid: str, ws: WebSocket, hidden: bool):
        self.caregiver_hidden.setdefault(sid, {})[ws] = hidden

    async def relay_to_caregivers(self, sid: str, msg: str):
        dead = []
        caregivers = self.caregivers.get(sid, [])
        for ws in list(caregivers):
            try: await ws.send_text(msg)
            except Exception: dead.append(ws)
        for ws in dead:
            try: caregivers.remove(ws)
            except ValueError: pass
            self.caregiver_hidden.get(sid, {}).pop(ws, None)
            
        caregivers_hidden = bool(caregivers) and all(
            self.caregiver_hidden.get(sid, {}).get(ws, False)
            for ws in caregivers
        )
        if not caregivers or caregivers_hidden:
            try:
                data = json.loads(msg)
                if data.get("type") == "chat" and data.get("sender") != "Caregiver":
                    send_web_push(sid, "Caregiver", f"Message from {data.get('sender', 'Patient')}", data.get("text", ""))
                elif data.get("type") == "alert":
                    send_web_push(sid, "Caregiver", "⚠️ Alert Triggered", data.get("message", "Check patient status!"))
            except Exception: pass

    async def relay_to_patient(self, sid: str, msg: str):
        ws = self.patients.get(sid)
        if ws:
            try: await ws.send_text(msg)
            except Exception: self.patients.pop(sid, None)
        if not ws or sid not in self.patients or self.patient_hidden.get(sid, False):
            try:
                data = json.loads(msg)
                if data.get("type") == "chat" and data.get("sender") == "Caregiver":
                    send_web_push(sid, "Patient", "Message from Caregiver", data.get("text", ""))
            except Exception: pass

    def patient_connected(self, sid: str) -> bool:
        return sid in self.patients

    def disconnect_patient(self, sid: str):
        self.patients.pop(sid, None)
        self.patient_hidden.pop(sid, None)
        print(f"❌ Patient disconnected: {sid}")

    def disconnect_caregiver(self, sid: str, ws: WebSocket):
        try: self.caregivers.get(sid, []).remove(ws)
        except ValueError: pass
        if sid in self.caregivers and not self.caregivers[sid]:
            self.caregivers.pop(sid, None)
        if sid in self.caregiver_hidden:
            self.caregiver_hidden[sid].pop(ws, None)
            if not self.caregiver_hidden[sid]:
                self.caregiver_hidden.pop(sid, None)

rtc = RTCManager()

# ── WebSocket: patient ────────────────────────────────────────────────────────

@app.websocket("/ws/patient/{session_id}")
async def patient_ws(websocket: WebSocket, session_id: str):
    await rtc.connect_patient(session_id, websocket)
    state = get_state(session_id)
    try:
        while True:
            raw = await websocket.receive_text()
            try: msg = json.loads(raw)
            except Exception: continue

            if msg.get("type") == "result":
                state.posture             = msg.get("posture",             state.posture)
                state.posture_severity    = msg.get("posture_severity",    state.posture_severity)
                state.expression_label    = msg.get("expression_label",    state.expression_label)
                state.expression_severity = msg.get("expression_severity", state.expression_severity)
                state.calibrating         = msg.get("calibrating",         state.calibrating)

                if not state.calibrating:
                    state.running = True

                analysis = state.to_analysis_msg()
                analysis["calib_remaining"] = msg.get("calib_remaining", 0)
                await rtc.relay_to_caregivers(session_id, json.dumps(analysis))
            elif msg.get("type") == "client_visibility":
                rtc.set_patient_visibility(session_id, bool(msg.get("hidden")))
            else:
                await rtc.relay_to_caregivers(session_id, raw)
    except WebSocketDisconnect:
        rtc.disconnect_patient(session_id)

# ── WebSocket: caregiver ──────────────────────────────────────────────────────

@app.websocket("/ws/caregiver/{session_id}")
async def caregiver_ws(websocket: WebSocket, session_id: str):
    await rtc.connect_caregiver(session_id, websocket)
    state = get_state(session_id)

    try: await websocket.send_text(json.dumps(state.to_analysis_msg()))
    except Exception: pass

    try:
        while True:
            data = await websocket.receive_text()
            try: msg = json.loads(data)
            except Exception: continue

            if msg.get("type") == "caregiver_ready":
                print(f"✅ Caregiver ready for session {session_id} — notifying patient")
                await rtc.relay_to_patient(session_id, json.dumps({
                    "type":      "caregiver_joined",
                    "timestamp": time.time(),
                }))
            elif msg.get("type") == "client_visibility":
                rtc.set_caregiver_visibility(session_id, websocket, bool(msg.get("hidden")))
            elif msg.get("type") == "chat":
                text = msg.get("text", "").strip()
                if text:
                    save_log(session_id, "Caregiver", text, "message")
                    await rtc.relay_to_patient(session_id, json.dumps({
                        "type":   "chat",
                        "sender": "Caregiver",
                        "text":   text,
                        "timestamp": time.strftime("%H:%M:%S"),
                    }))
            else:
                await rtc.relay_to_patient(session_id, data)
    except WebSocketDisconnect:
        rtc.disconnect_caregiver(session_id, websocket)

# ── Page helpers ──────────────────────────────────────────────────────────────

def _page(name: str) -> FileResponse:
    r = FileResponse(os.path.join(FRONTEND_PATH, name))
    r.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    return r

def _require_user(request: Request):
    return request.session.get("user")

def _is_session_live(sid: str) -> bool:
    state = _session_states.get(sid)
    if state is not None:
        return state.session_active
    row = find_session_by_id(sid)
    if not row:
        return False
    return bool(row.get("active", 0))

def _json_datetime(value):
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value

def _dashboard_payload(row):
    if not row:
        return None
    item = dict(row)
    for key in ("session_created_at", "session_ended_at", "created_at"):
        item[key] = _json_datetime(item.get(key))
    top_alerts = item.get("top_alerts") or []
    if isinstance(top_alerts, str):
        try:
            top_alerts = json.loads(top_alerts)
        except Exception:
            top_alerts = []
    item["top_alerts"] = top_alerts
    return item

# ── Page routes ───────────────────────────────────────────────────────────────

@app.get("/")
def home(): return _page("home.html")

@app.get("/auth")
def auth_page(): return _page("auth.html")

@app.get("/auth/check")
def auth_check(request: Request):
    user = _require_user(request)
    return {"authenticated": user is not None, "role": user.get("role") if user else None, "name": user.get("name") if user else None}

@app.get("/profile")
def profile_page(request: Request):
    if not _require_user(request): return RedirectResponse("/auth")
    return _page("profile.html")

@app.get("/dashboard")
def dashboard_page(request: Request):
    if not _require_user(request): return RedirectResponse("/auth")
    return _page("dashboard.html")

@app.post("/logout")
def logout(request: Request): request.session.clear(); return {"status": "logged_out"}

@app.get("/setup")
def setup(request: Request):
    if not _require_user(request): return RedirectResponse("/auth")
    return _page("setup.html")

@app.get("/monitor")
def monitor(request: Request):
    if not _require_user(request):
        return RedirectResponse("/auth")
    
    request.session.pop("monitoring_session", None)
    return _page("monitor.html")

@app.get("/patient")
def patient(request: Request):
    user = _require_user(request)
    if not user: return RedirectResponse("/auth")
    return _page("index.html")

@app.get("/caregiver")
def caregiver(request: Request):
    if not _require_user(request):
        return RedirectResponse("/auth")
        
    sid = request.session.get("monitoring_session")
    

    if not sid:
        return RedirectResponse("/monitor")
        
    if not _is_session_live(sid):
        request.session.pop("monitoring_session", None)
        return RedirectResponse("/monitor")
        
    return _page("caregiver.html")

# ── Session control ───────────────────────────────────────────────────────────

@app.post("/ready/{session_id}")
async def ready(session_id: str):
    state                = get_state(session_id)
    state.running        = True
    state.session_active = True
    state.calib_start    = time.time()
    state.calibrating    = True

    msg = json.dumps({"type": "control", "action": "ready"})
    await rtc.relay_to_patient(session_id, msg)
    await rtc.relay_to_caregivers(session_id, msg)
    return {"status": "running"}


@app.post("/pause/{session_id}")
async def pause(session_id: str):
    state         = get_state(session_id)
    state.running = False

    msg = json.dumps({"type": "control", "action": "pause"})
    await rtc.relay_to_patient(session_id, msg)
    await rtc.relay_to_caregivers(session_id, msg)
    return {"status": "paused"}


@app.post("/resume/{session_id}")
async def resume(session_id: str):
    state         = get_state(session_id)
    state.running = True

    msg = json.dumps({"type": "control", "action": "resume"})
    await rtc.relay_to_patient(session_id, msg)
    await rtc.relay_to_caregivers(session_id, msg)
    return {"status": "running"}


@app.post("/leave-session")
def leave_session(request: Request):
    """Caregiver leaves monitoring without ending the patient's session."""
    sid = get_monitoring_sid(request)
    request.session.pop("monitoring_session", None)
    return JSONResponse({"status": "left", "session_id": sid})


@app.post("/end-session")
async def end(request: Request):
    sid = get_active_sid(request)
    if not sid:
        return JSONResponse({"status": "error", "message": "No active session"})

    user = _require_user(request)
    dashboard = None
    try:
        owner_row = None
        with get_db() as conn:
            c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            c.execute("SELECT owner_user_id FROM sessions WHERE session_id = %s", (sid,))
            owner_row = c.fetchone()
        user_id = user.get("id") if user else None
        dashboard = create_session_dashboard(sid, user_id)
        owner_user_id = owner_row.get("owner_user_id") if owner_row else None
        if owner_user_id and owner_user_id != user_id:
            create_session_dashboard(sid, owner_user_id)
    except Exception as e:
        print(f"Dashboard creation error: {e}")

    try: end_session_db(sid)
    except Exception as e: print(f"End session DB error: {e}")

    state = get_state(sid)
    state.running        = False
    state.session_active = False
    state.calibrating    = False

    msg = json.dumps({"type": "control", "action": "ended"})
    await rtc.relay_to_patient(sid, msg)
    await rtc.relay_to_caregivers(sid, msg)

    request.session.pop("monitoring_session", None)
    request.session.pop("patient_session", None)
    request.session["latest_dashboard_session"] = sid
    request.session["dashboard_notice"] = "A dashboard was created for the session. Check the profile icon to see info."
    _session_states.pop(sid, None)
    
    return JSONResponse({"status": "ended", "dashboard": _dashboard_payload(dashboard)})


@app.post("/patient-leave")
async def patient_leave(request: Request):
    sid = request.session.get("patient_session")
    if not sid:
        return JSONResponse({"status": "error", "message": "No active session"})

    state = get_state(sid)
    state.running = False
    state.patient_online = False

    msg = json.dumps({"type": "control", "action": "patient_left"})
    await rtc.relay_to_caregivers(sid, msg)

    request.session.pop("patient_session", None)
    return JSONResponse({"status": "left"})


@app.post("/rejoin-session")
def rejoin_session(data: dict, request: Request):
    sid = data.get("session_id", "").strip()
    sp_id = data.get("sp_id", "").strip()
    
    if not sid or not sp_id:
        return {"status": "error", "message": "Session ID and SP ID are required."}
        
    if verify_sp_id(sid, sp_id):
        request.session["patient_session"] = sid
        state = get_state(sid)
        state.patient_online = True
        return {"status": "ok", "session_id": sid}
        
    return {"status": "error", "message": "Invalid Session ID or SP ID, or session has ended."}


@app.post("/rejoin-session-by-info")
def rejoin_session_by_info(data: dict, request: Request):
    name = data.get("name", "").strip()
    age = data.get("age")
    gender = data.get("gender", "")
    date_str = data.get("date", "")
    sp_id = data.get("sp_id", "").strip()

    if not name or not sp_id or not date_str:
        return {"status": "error", "message": "Name, Date, and SP ID are required."}

    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        query = """
            SELECT session_id FROM sessions 
            WHERE patient_name ILIKE %s AND sp_id = %s 
            AND DATE(created_at) = %s AND active = 1
        """
        params = [f"%{name}%", sp_id, date_str]
        
        if age:
            query += " AND age = %s"
            params.append(age)
        if gender:
            query += " AND gender = %s"
            params.append(gender)
            
        query += " ORDER BY created_at DESC"
        
        c.execute(query, params)
        results = c.fetchall()
        
    if len(results) >= 1:
        sid = results[0]["session_id"]
        request.session["patient_session"] = sid
        state = get_state(sid)
        state.patient_online = True
        return {"status": "ok", "session_id": sid}
    else:
        return {"status": "error", "message": "No active session found matching these details."}


@app.post("/start-session")
def start_session_route(data: dict, request: Request):
    name    = data.get("name",    "").strip()
    sp_id   = data.get("sp_id",   "").strip()
    age     = data.get("age",     0)
    gender  = data.get("gender",  "")
    disease = data.get("disease", "")

    if not name or not sp_id:
        return {"status": "rejected", "message": "Name and sp_id are required"}

    with get_db() as conn:
        conn.cursor().execute("UPDATE sessions SET active = 0 WHERE active = 1 AND created_at < NOW() - INTERVAL '12 hours'")

    new_session_id = "SES-" + str(random.randint(100000, 999999))
    user = _require_user(request)
    save_session(new_session_id, sp_id, name, age, gender, disease, user.get("id") if user else None)
    request.session["patient_session"] = new_session_id

    new_state                     = SessionState()
    new_state.posture             = "CALIBRATING"
    new_state.posture_severity    = "NORMAL"
    new_state.expression_label    = "⏳ Calibrating"
    new_state.expression_severity = "NORMAL"
    new_state.calibrating         = True
    new_state.calib_start         = None
    new_state.running             = False
    new_state.session_active      = True
    _session_states[new_session_id] = new_state

    print(f"🟢 Session: {new_session_id} | Patient: {name}")
    return {"status": "started", "session_id": new_session_id}


@app.get("/current-session")
def current_session(request: Request):
    if not _require_user(request): return {"session_id": None}
    return {"session_id": request.session.get("patient_session")}


@app.get("/status")
def get_status(request: Request):
    sid = get_active_sid(request)
    if not sid: return {"running": False, "session_active": False}
    
    state = get_state(sid)
    return {
        "running":             state.running,
        "session_active":      state.session_active,
        "patient_online":      state.patient_online,
        "calibrating":         state.calibrating,
        "posture":             state.posture,
        "posture_severity":    state.posture_severity,
        "expression_label":    state.expression_label,
        "expression_severity": state.expression_severity,
    }

# ── Search & verify ───────────────────────────────────────────────────────────

@app.get("/search-patient")
def search_patient(name: str = "", age: int = None, gender: str = ""):
    if not name: return {"results": []}
    return {"results": [{"session_id": r["session_id"], "patient_name": r["patient_name"], "age": r["age"], "gender": r["gender"], "disease": r["disease"], "created_at": r["created_at"]} for r in find_sessions_by_patient(name, age or None, gender or None)]}


@app.get("/search-session")
def search_session(session_id: str = ""):
    if not session_id: return {"result": None}
    row = find_session_by_id(session_id)
    if not row: return {"result": None}
    return {"result": {"session_id": row["session_id"], "patient_name": row["patient_name"], "age": row["age"], "gender": row["gender"], "disease": row["disease"], "created_at": row["created_at"]}}


@app.post("/verify-access")
def verify_access(data: dict, request: Request):
    sid   = data.get("session_id", "").strip()
    sp_id = data.get("sp_id",      "").strip()
    if not sid or not sp_id: return {"status": "error", "message": "Missing fields"}
    if verify_sp_id(sid, sp_id):
        request.session["monitoring_session"] = sid
        return {"status": "success"}
    return {"status": "error", "message": "Invalid special ID."}


@app.get("/monitored-session")
def monitored_session(request: Request):
    sid = request.session.get("monitoring_session")
    if not sid: return {"patient_name": "Patient", "session_id": None}
    row = find_session_by_id(sid)
    if not row: return {"patient_name": "Patient", "session_id": sid}
    return {"patient_name": row["patient_name"], "session_id": row["session_id"], "age": row["age"], "gender": row["gender"]}

# ── Web Push API ──────────────────────────────────────────────────────────────

@app.get("/api/profile")
def api_profile(request: Request):
    user = _require_user(request)
    if not user:
        return JSONResponse({"status": "error", "message": "Authentication required"}, status_code=401)
    profile = get_user_profile(user["id"])
    if not profile:
        return JSONResponse({"status": "error", "message": "User not found"}, status_code=404)
    dashboards = [_dashboard_payload(r) for r in get_dashboards_for_user(user["id"])]
    return {
        "status": "ok",
        "user": profile,
        "label": "Professional" if profile.get("role") == "professional" else "Home user",
        "dashboards": dashboards,
    }


@app.post("/api/profile")
def api_update_profile(data: dict, request: Request):
    user = _require_user(request)
    if not user:
        return JSONResponse({"status": "error", "message": "Authentication required"}, status_code=401)
    age = data.get("age")
    role_value = (data.get("role") or data.get("job_position") or "").strip()
    phone = data.get("phone")
    try:
        age = int(age) if age not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse({"status": "error", "message": "Age must be a number"}, status_code=400)
    profile = get_user_profile(user["id"])
    job_position = role_value if profile and profile.get("role") == "professional" else None
    
    # Clean phone number (strip whitespace or set to None if key is missing/null)
    phone_value = phone.strip() if phone is not None else None
    
    updated = update_user_profile(user["id"], age=age, job_position=job_position, phone=phone_value)
    return {"status": "ok", "user": updated}


@app.get("/api/dashboard/latest")
def api_latest_dashboard(request: Request):
    user = _require_user(request)
    if not user:
        return JSONResponse({"status": "error", "message": "Authentication required"}, status_code=401)
    sid = request.session.get("latest_dashboard_session")
    row = get_session_dashboard(sid, user["id"]) if sid else None
    if not row:
        dashboards = get_dashboards_for_user(user["id"])
        row = dashboards[0] if dashboards else None
    return {"status": "ok", "dashboard": _dashboard_payload(row)}


@app.get("/api/dashboard-notice")
def api_dashboard_notice(request: Request):
    user = _require_user(request)
    if not user:
        return {"notice": None}
    notice = request.session.pop("dashboard_notice", None)
    return {"notice": notice}


@app.get("/api/vapidPublicKey")
def get_vapid_public_key():
    vapid = get_vapid_status()
    if not vapid["public_key_configured"] or not vapid["public_key_valid_shape"]:
        return JSONResponse(
            {"status": "error", "message": "VAPID public key is missing or invalid", "issues": vapid["issues"]},
            status_code=500,
        )
    return Response(content=VAPID_PUBLIC_KEY, media_type="text/plain")

@app.post("/api/subscribe")
async def subscribe_push(request: Request):
    try:
        sub = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "message": "Invalid JSON"}, status_code=400)
        
    sid = get_active_sid(request)
    if not sid:
        return JSONResponse({"status": "error", "message": "No active session"}, status_code=401)
        
    role = "Caregiver" if request.session.get("monitoring_session") else "Patient"
    push_subscriptions[(sid, role)] = sub
    try:
        save_push_subscription(sid, role, sub)
    except Exception as exc:
        print(f"âš ï¸ Failed to save push subscription: {exc}")
        return JSONResponse({"status": "error", "message": f"Could not save subscription: {exc}"}, status_code=500)
    print(f"🔔 Registered Push Subscription for {role} in {sid}")
    return {"status": "success"}

# ── Auth ──────────────────────────────────────────────────────────────────────

@app.get("/api/notification-debug")
def notification_debug(request: Request, test: bool = False):
    sid = get_active_sid(request)
    role = "Caregiver" if request.session.get("monitoring_session") else "Patient"
    vapid = get_vapid_status()
    result = {
        "status": "ok",
        "origin": str(request.base_url).rstrip("/"),
        "secure_request": request.url.scheme == "https",
        "session_id": sid,
        "role": role,
        "vapid": vapid,
        "stored_subscription_count": 0,
        "memory_subscription_present": False,
        "test_push": None,
        "issues": list(vapid["issues"]),
    }
    if not sid:
        result["issues"].append("No active patient/caregiver session cookie reached the backend")
        return result

    try:
        result["stored_subscription_count"] = count_push_subscriptions(sid, role)
    except Exception as exc:
        result["issues"].append(f"Could not query saved subscriptions: {exc}")

    mem_sub = push_subscriptions.get((sid, role))
    result["memory_subscription_present"] = bool(mem_sub)

    if result["stored_subscription_count"] == 0 and not mem_sub:
        result["issues"].append("No push subscription is saved for this session and role")

    if test:
        if not vapid["ready"]:
            result["test_push"] = {"status": "skipped", "message": "VAPID/webpush is not ready"}
        elif result["stored_subscription_count"] == 0 and not mem_sub:
            result["test_push"] = {"status": "skipped", "message": "No saved subscription to send to"}
        else:
            try:
                send_web_push(sid, role, "InclusiveBridge test", "Notification test from server")
                result["test_push"] = {"status": "attempted"}
            except Exception as exc:
                result["test_push"] = {"status": "error", "message": str(exc)}
                result["issues"].append(f"Test push raised an exception: {exc}")

    return result

def is_valid_email(email: str) -> bool:
    pattern = r"^[\w\.-]+@[\w\.-]+\.\w+$"
    return bool(re.match(pattern, email))

@app.post("/auth/register")
async def register(data: dict, request: Request):
    email = data.get("email", "").strip()
    password = data.get("password", "")
    name = data.get("name", "").strip()

    if not name or not email or not password:
        return {"status": "error", "message": "Please fill in all required fields."}

    if not is_valid_email(email):
        return {"status": "error", "message": "Invalid email format."}

    # Check if email is already registered
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email = %s", (email,))
        if c.fetchone():
            return {"status": "error", "message": "Email already registered."}

    otp = generate_otp()
    if not await send_otp_email_async(email, otp): return {"status": "error", "message": "Failed to send OTP — check server email config."}
    store_otp(email, otp)
    data_copy             = dict(data)
    data_copy["password"] = hash_password(password)
    request.session["temp_user"] = data_copy
    return {"status": "otp_sent"}

@app.post("/auth/verify-register")
async def verify_register(data: dict, request: Request):
    if not verify_otp(data["email"], data["otp"]): return {"status": "error", "message": "Invalid or expired OTP."}
    user = request.session.get("temp_user")
    if not user: return {"status": "error", "message": "Session expired — please register again."}
    try:
        with get_db() as conn:
            c = conn.cursor()
            c.execute("""INSERT INTO users (name, age, gender, email, password, phone, role, caregiver_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""", (user["name"], user["age"], user["gender"], user["email"], user["password"], user.get("phone", ""), user["role"], user.get("caregiver_id", "")))
            uid = c.fetchone()[0]
    except psycopg2.errors.UniqueViolation: return {"status": "error", "message": "Email already registered."}
    request.session["user"] = {"id": uid, "role": user["role"], "name": user["name"]}
    request.session.pop("temp_user", None)
    return {"status": "registered", "role": user["role"]}

@app.post("/auth/login")
async def login(data: dict, request: Request):
    email = data.get("email", "").strip()
    password = data.get("password", "")

    if not email or not password:
        return {"status": "error", "message": "Email and password are required."}

    if not is_valid_email(email):
        return {"status": "error", "message": "Invalid email format."}

    with get_db() as conn:
        c = conn.cursor()
        if data["role"] == "professional":
            c.execute("""SELECT id, role, name, password FROM users WHERE email = %s AND role = %s AND caregiver_id = %s""",
                      (email, data["role"], data.get("caregiver_id", "")))
        else:
            c.execute("""SELECT id, role, name, password FROM users WHERE email = %s AND role = %s""",
                      (email, data["role"]))
        user = c.fetchone()

    if not user: return {"status": "error", "message": "Invalid credentials."}
    stored_pw = user[3]
    if ":" not in stored_pw: return {"status": "error", "message": "Your account uses an old format. Please re-register."}
    if not check_password(data["password"], stored_pw): return {"status": "error", "message": "Invalid credentials."}

    otp = generate_otp()
    if not await send_otp_email_async(data["email"], otp): return {"status": "error", "message": "Failed to send OTP. Contact support."}
    store_otp(data["email"], otp)
    request.session["pending_email"] = data["email"]
    return {"status": "otp_sent"}

@app.post("/auth/verify-login")
async def verify_login(data: dict, request: Request):
    email = request.session.get("pending_email")
    if not email or not verify_otp(email, data["otp"]): return {"status": "error", "message": "Invalid or expired OTP."}
    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id, role, name FROM users WHERE email = %s", (email,))
        user = c.fetchone()
    if not user: return {"status": "error", "message": "User not found."}
    request.session["user"] = {"id": user[0], "role": user[1], "name": user[2]}
    request.session.pop("pending_email", None)
    return {"status": "success", "user_id": user[0], "role": user[1]}

@app.post("/auth/forgot-password")
async def forgot_password(data: dict):
    email = data.get("email", "").strip()
    if not email:
        return {"status": "error", "message": "Email is required."}

    if not is_valid_email(email):
        return {"status": "error", "message": "Invalid email format."}

    with get_db() as conn:
        c = conn.cursor()
        c.execute("SELECT id FROM users WHERE email = %s", (email,))
        if not c.fetchone(): return {"status": "error", "message": "Email not found."}
    
    otp = generate_otp()
    if not await send_otp_email_async(email, otp): return {"status": "error", "message": "Failed to send OTP."}
    store_otp(email, otp)
    return {"status": "otp_sent"}

@app.post("/auth/verify-reset-otp")
async def verify_reset_otp(data: dict, request: Request):
    """Step 1: Verify the OTP for password reset. Stores verified email in session."""
    email = data.get("email", "").strip()
    otp   = data.get("otp", "").strip()
    if not verify_otp(email, otp):
        return {"status": "error", "message": "Invalid or expired code. Please try again."}
    # Store the verified email in session so step 2 can trust it
    request.session["reset_verified_email"] = email
    return {"status": "verified"}

@app.post("/auth/reset-password")
async def reset_password(data: dict, request: Request):
    """Step 2: Set the new password. Requires OTP to have been verified in step 1."""
    verified_email = request.session.get("reset_verified_email", "")
    email = data.get("email", "").strip()
    new_pw = data.get("new_password", "")

    # Guard: must match what was OTP-verified
    if not verified_email or verified_email != email:
        return {"status": "error", "message": "Session expired or email mismatch. Please restart the process."}

    hashed_pw = hash_password(new_pw)
    with get_db() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET password = %s WHERE email = %s", (hashed_pw, email))
    # Clear the verified marker
    request.session.pop("reset_verified_email", None)
    return {"status": "success"}

# ── Chat & history ────────────────────────────────────────────────────────────

def _format_chat_row(row: dict) -> dict:
    item = dict(row)
    item["msg_type"] = item.get("type") or "message"
    return item


@app.get("/chat")
def get_chat(request: Request):
    sid = get_monitoring_sid(request) or get_patient_sid(request)
    if not sid: return []
    with get_db() as conn:
        c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        c.execute(
            "SELECT sender, message, timestamp, type FROM logs WHERE session_id = %s ORDER BY id ASC",
            (sid,),
        )
        return [_format_chat_row(r) for r in c.fetchall()]


@app.get("/chat/voice/{filename}")
def get_voice_file(filename: str, request: Request):
    safe = os.path.basename(filename)
    row = get_voice_file_db(safe)

    if not row:
        # Fallback to filesystem
        filepath = os.path.join(VOICE_DIR, safe)
        if os.path.isfile(filepath):
            media = "audio/mp4" if safe.endswith(".m4a") else "audio/webm"
            return FileResponse(
                filepath,
                media_type=media,
                headers={"Cache-Control": "public, max-age=86400", "Accept-Ranges": "bytes"},
            )
        return JSONResponse({"status": "error", "message": "Not found"}, status_code=404)

    audio_data: bytes = row["data"]
    mime: str = row["mime_type"] or "audio/webm"
    total = len(audio_data)

    range_header = request.headers.get("range")
    if range_header:
        # Parse "bytes=start-end"
        try:
            unit, rng = range_header.split("=")
            start_str, end_str = rng.split("-")
            start = int(start_str)
            end   = int(end_str) if end_str else total - 1
        except Exception:
            return Response(status_code=400)

        end   = min(end, total - 1)
        chunk = audio_data[start : end + 1]
        return Response(
            content=chunk,
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range":  f"bytes {start}-{end}/{total}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(len(chunk)),
                "Cache-Control":  "public, max-age=86400",
            },
        )

    # Full response
    return Response(
        content=audio_data,
        media_type=mime,
        headers={
            "Accept-Ranges":  "bytes",
            "Content-Length": str(total),
            "Cache-Control":  "public, max-age=86400",
        },
    )




@app.post("/chat/send-voice")
async def send_voice(
    request: Request,
    file: UploadFile = File(...),
    sender: str = Form("Patient"),
):
    sender = sender.strip() or "Patient"
    if sender == "Caregiver":
        sid = get_monitoring_sid(request)
    else:
        sid = get_patient_sid(request)
    if not sid:
        return JSONResponse({"status": "error", "message": "No session attached"}, status_code=400)

    data = await file.read()
    if not data:
        return JSONResponse({"status": "error", "message": "Empty audio file"}, status_code=400)
    if len(data) > 2 * 1024 * 1024:
        return JSONResponse({"status": "error", "message": "Voice message too large (max 2 MB)"}, status_code=400)

    voice_id = str(uuid.uuid4())
    ext = ".webm"
    mime = "audio/webm"
    if file.content_type and "mp4" in file.content_type:
        ext, mime = ".m4a", "audio/mp4"
    elif file.filename and file.filename.endswith(".m4a"):
        ext, mime = ".m4a", "audio/mp4"

    file_id = f"{voice_id}{ext}"
    save_voice_file(file_id, sid, mime, data)

    voice_url = f"/chat/voice/{file_id}"
    save_log(sid, sender, voice_url, "voice")

    push = json.dumps({
        "type": "chat",
        "sender": sender,
        "text": voice_url,
        "message_type": "voice",
        "timestamp": time.strftime("%H:%M:%S"),
    })
    if sender == "Patient":
        await rtc.relay_to_caregivers(sid, push)
    else:
        await rtc.relay_to_patient(sid, push)

    return {"status": "ok", "url": voice_url}

@app.post("/chat/send")
async def send_chat(data: dict, request: Request):
    sender = data.get("sender", "Patient")
    if sender == "Caregiver":
        sid = get_monitoring_sid(request)
    else:
        sid = get_patient_sid(request)
    if not sid:
        return {"status": "error", "message": "No session attached"}
    text   = data.get("text",   "").strip()
    if not text: return {"status": "ok"}

    save_log(sid, sender, text, "message")
    push = json.dumps({"type": "chat", "sender": sender, "text": text, "timestamp": time.strftime("%H:%M:%S")})

    if sender == "Patient": await rtc.relay_to_caregivers(sid, push)
    else: await rtc.relay_to_patient(sid, push)
    return {"status": "ok"}


# ── AI Assistant ──────────────────────────────────────────────────────────────

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
_gemini_model = None

def _get_gemini():
    global _gemini_model
    if not GEMINI_API_KEY:
        return None
    if _gemini_model is None:
        try:
            import google.generativeai as genai
            genai.configure(api_key=GEMINI_API_KEY)
            _gemini_model = genai.GenerativeModel(
                model_name="gemini-2.5-flash",
                system_instruction=(
                    "You are an AI assistant embedded in InclusiveBridge — a real-time patient monitoring app. "
                    "You have access to the live chat and alert history for the current session. "
                    "Be concise, clinically helpful, and empathetic. "
                    "When asked about alerts or chat events, analyse the provided chat context accurately. "
                    "If the user asks you to SEND A MESSAGE, SAY SOMETHING, OR REPLY to the patient/chat immediately, you MUST return a JSON object: {\"type\":\"reminder\",\"message\":\"your message\",\"delay_seconds\":0}. Do NOT reply conversationally."
                    "For all other responses return plain readable text."
                ),
            )
        except Exception as e:
            print(f"⚠️ Gemini init failed: {e}")
            return None
    return _gemini_model


@app.post("/ai/chat")
async def ai_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"status": "error", "reply": "Invalid request body."}, status_code=400)

    prompt      = (body.get("prompt") or "").strip()
    chat_ctx    = body.get("context", [])   # list of {sender, message, timestamp}
    image_b64   = body.get("image")         # base64 string or None
    image_mime  = body.get("image_mime", "image/jpeg")

    if not prompt:
        return JSONResponse({"status": "error", "reply": "Empty prompt."}, status_code=400)

    model = _get_gemini()
    if model is None:
        return JSONResponse({
            "status": "error",
            "reply": "⚠️ AI assistant is not configured yet. Ask your admin to set the GEMINI_API_KEY environment variable."
        }, status_code=503)

    # Build context block from chat history
    ctx_lines = []
    for m in chat_ctx[-40:]:   # last 40 messages max
        sender  = m.get("sender", "?")
        message = m.get("message", "")
        ts      = m.get("timestamp", "")
        ctx_lines.append(f"[{ts}] {sender}: {message}")
    context_block = "\n".join(ctx_lines) if ctx_lines else "(no chat history yet)"

    full_prompt = (
        f"=== CURRENT SESSION CHAT LOG ===\n{context_block}\n"
        f"=== USER REQUEST ===\n{prompt}"
    )

    try:
        import google.generativeai as genai

        parts = [full_prompt]

        if image_b64:
            import base64
            img_bytes = base64.b64decode(image_b64)
            parts = [
                {"mime_type": image_mime, "data": img_bytes},
                full_prompt,
            ]

        response = model.generate_content(parts)
        reply_text = response.text.strip()

        # Check if Gemini returned a reminder JSON
        import re
        json_match = re.search(r'\{.*?"type"\s*:\s*"reminder".*?\}', reply_text, re.DOTALL)
        if json_match:
            try:
                reminder_data = json.loads(json_match.group())
                
                # Strip the raw JSON block from the reply text so it isn't rendered
                clean_reply = reply_text.replace(json_match.group(), "").strip()
                # If there's nothing left but markdown backticks, clean them
                clean_reply = re.sub(r'^```json\s*|```$', '', clean_reply).strip()
                if not clean_reply:
                    clean_reply = f"✅ I will send this message directly to the chat!"
                
                return JSONResponse({"status": "ok", "reply": clean_reply, "reminder": reminder_data})
            except Exception:
                pass

        return JSONResponse({"status": "ok", "reply": reply_text})

    except Exception as e:
        print(f"⚠️ Gemini API error: {e}")
        return JSONResponse({"status": "error", "reply": f"AI error: {str(e)}"}, status_code=500)


# ── Run ───────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    import socket
    import ipaddress
    
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try: s.connect(("8.8.8.8", 80)); local_ip = s.getsockname()[0]
    except Exception: local_ip = "127.0.0.1"
    finally: s.close()

    cert_file = os.path.join(BASE_DIR, "cert.pem")
    key_file = os.path.join(BASE_DIR, "key.pem")
    
    if not os.path.exists(cert_file) or not os.path.exists(key_file):
        try:
            from cryptography import x509
            from cryptography.x509.oid import NameOID
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.hazmat.primitives import serialization
            import datetime
            
            print("Generating self-signed certificate for HTTPS...")
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            with open(key_file, "wb") as f:
                f.write(key.private_bytes(
                    encoding=serialization.Encoding.PEM,
                    format=serialization.PrivateFormat.TraditionalOpenSSL,
                    encryption_algorithm=serialization.NoEncryption()
                ))
            
            subject = issuer = x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, u"InclusiveBridge"),
            ])
            cert = (
                x509.CertificateBuilder()
                .subject_name(subject)
                .issuer_name(issuer)
                .public_key(key.public_key())
                .serial_number(x509.random_serial_number())
                .not_valid_before(datetime.datetime.utcnow())
                .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
                .add_extension(
                    x509.SubjectAlternativeName([
                        x509.DNSName(u"localhost"),
                        x509.IPAddress(ipaddress.IPv4Address(local_ip))
                    ]),
                    critical=False,
                )
                .sign(key, hashes.SHA256())
            )
            
            with open(cert_file, "wb") as f:
                f.write(cert.public_bytes(serialization.Encoding.PEM))
        except Exception as e:
            print(f"⚠️ Could not generate certificate: {e}")

    ssl_kwargs = {}
    protocol = "http"
    if os.path.exists(cert_file) and os.path.exists(key_file):
        ssl_kwargs = {"ssl_keyfile": key_file, "ssl_certfile": cert_file}
        protocol = "https"

    print("🚀 InclusiveBridge starting…")
    print(f"📍 Patient   → {protocol}://localhost:8000/patient")
    print(f"📡 Caregiver → {protocol}://{local_ip}:8000/caregiver")
    webbrowser.open(f"{protocol}://localhost:8000")
    uvicorn.run(app, host="0.0.0.0", port=8000, **ssl_kwargs)
