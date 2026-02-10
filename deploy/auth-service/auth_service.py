"""
ATLAS Auth Service - Minimal multi-tenant authentication.

Provides cookie-based auth with bcrypt password hashing and HMAC-signed session cookies.
User accounts are stored in a JSON file on disk.
"""

import hashlib
import hmac
import json
import os
import time
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from passlib.hash import bcrypt

app = FastAPI()

DATA_DIR = Path(os.environ.get("AUTH_DATA_DIR", "/data"))
USERS_FILE = DATA_DIR / "users.json"
SECRET = os.environ["ATLAS_AUTH_SECRET"]
SESSION_HOURS = int(os.environ.get("ATLAS_AUTH_SESSION_HOURS", "24"))
COOKIE_NAME = "atlas_session"


# ---------------------------------------------------------------------------
# User storage helpers
# ---------------------------------------------------------------------------

def _load_users() -> dict:
    if not USERS_FILE.exists():
        return {}
    with open(USERS_FILE) as f:
        return json.load(f)


def _save_users(users: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = USERS_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(users, f, indent=2)
    tmp.rename(USERS_FILE)


# ---------------------------------------------------------------------------
# Cookie helpers
# ---------------------------------------------------------------------------

def _sign(username: str, ts: str) -> str:
    msg = f"{username}:{ts}"
    return hmac.new(SECRET.encode(), msg.encode(), hashlib.sha256).hexdigest()


def _make_cookie(username: str) -> str:
    ts = str(int(time.time()))
    sig = _sign(username, ts)
    return f"{username}:{ts}:{sig}"


def _verify_cookie(cookie: str) -> str | None:
    """Return username if valid, else None."""
    parts = cookie.split(":", 2)
    if len(parts) != 3:
        return None
    username, ts, sig = parts
    if not hmac.compare_digest(sig, _sign(username, ts)):
        return None
    try:
        age = time.time() - int(ts)
    except ValueError:
        return None
    if age > SESSION_HOURS * 3600 or age < 0:
        return None
    return username


# ---------------------------------------------------------------------------
# HTML templates (dark theme, minimal)
# ---------------------------------------------------------------------------

_STYLE = """
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         background: #1a1a2e; color: #e0e0e0; display: flex; align-items: center;
         justify-content: center; min-height: 100vh; }
  .card { background: #16213e; border-radius: 12px; padding: 2.5rem;
          width: 100%; max-width: 400px; box-shadow: 0 8px 32px rgba(0,0,0,.4); }
  h1 { text-align: center; margin-bottom: 1.5rem; color: #e0e0e0; font-size: 1.5rem; }
  label { display: block; margin-bottom: .3rem; font-size: .9rem; color: #a0a0b0; }
  input[type=text], input[type=password], input[type=email] {
    width: 100%; padding: .7rem; margin-bottom: 1rem; border: 1px solid #2a2a4a;
    border-radius: 6px; background: #0f3460; color: #e0e0e0; font-size: 1rem; }
  input:focus { outline: none; border-color: #5388d8; }
  button { width: 100%; padding: .75rem; border: none; border-radius: 6px;
           background: #5388d8; color: #fff; font-size: 1rem; cursor: pointer;
           font-weight: 600; }
  button:hover { background: #4070c0; }
  .link { text-align: center; margin-top: 1rem; }
  .link a { color: #5388d8; text-decoration: none; }
  .link a:hover { text-decoration: underline; }
  .error { background: #3a1a1a; border: 1px solid #8b3a3a; color: #ff8080;
           padding: .6rem; border-radius: 6px; margin-bottom: 1rem; text-align: center;
           font-size: .9rem; }
  .success { background: #1a3a1a; border: 1px solid #3a8b3a; color: #80ff80;
             padding: .6rem; border-radius: 6px; margin-bottom: 1rem; text-align: center;
             font-size: .9rem; }
</style>
"""


def _login_page(error: str = "", message: str = "") -> str:
    err_html = f'<div class="error">{error}</div>' if error else ""
    msg_html = f'<div class="success">{message}</div>' if message else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>ATLAS Login</title>{_STYLE}</head>
<body><div class="card"><h1>ATLAS Login</h1>{err_html}{msg_html}
<form method="post" action="/login">
  <label for="username">Email</label>
  <input type="email" id="username" name="username" required autofocus>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required>
  <button type="submit">Log in</button>
</form>
<div class="link"><a href="/signup">Create an account</a></div>
</div></body></html>"""


def _signup_page(error: str = "") -> str:
    err_html = f'<div class="error">{error}</div>' if error else ""
    return f"""<!DOCTYPE html><html><head><meta charset="utf-8"><title>ATLAS Sign Up</title>{_STYLE}</head>
<body><div class="card"><h1>Create Account</h1>{err_html}
<form method="post" action="/signup">
  <label for="username">Email</label>
  <input type="email" id="username" name="username" required autofocus>
  <label for="password">Password</label>
  <input type="password" id="password" name="password" required minlength="8">
  <button type="submit">Sign up</button>
</form>
<div class="link"><a href="/login">Already have an account? Log in</a></div>
</div></body></html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    msg = request.query_params.get("msg", "")
    return _login_page(message=msg)


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip().lower()
    password = form.get("password") or ""

    if not username or not password:
        return HTMLResponse(_login_page(error="Email and password are required."), status_code=400)

    users = _load_users()
    user = users.get(username)
    if not user or not bcrypt.verify(password, user["password_hash"]):
        return HTMLResponse(_login_page(error="Invalid email or password."), status_code=401)

    response = RedirectResponse("/", status_code=302)
    response.set_cookie(
        COOKIE_NAME, _make_cookie(username),
        max_age=SESSION_HOURS * 3600, httponly=True, samesite="lax", path="/",
    )
    return response


@app.get("/signup", response_class=HTMLResponse)
async def signup_page():
    return _signup_page()


@app.post("/signup")
async def signup_submit(request: Request):
    form = await request.form()
    username = (form.get("username") or "").strip().lower()
    password = form.get("password") or ""

    if not username or not password:
        return HTMLResponse(_signup_page(error="Email and password are required."), status_code=400)
    if len(password) < 8:
        return HTMLResponse(_signup_page(error="Password must be at least 8 characters."), status_code=400)

    users = _load_users()
    if username in users:
        return HTMLResponse(_signup_page(error="An account with that email already exists."), status_code=409)

    users[username] = {
        "username": username,
        "password_hash": bcrypt.hash(password),
    }
    _save_users(users)

    return RedirectResponse("/login?msg=Account+created.+Please+log+in.", status_code=302)


@app.get("/auth")
async def auth_check(request: Request):
    """Validate session cookie.

    Returns 200 + X-User-Email on success.
    Returns 302 redirect to /login on failure. Builds an absolute URL from
    X-Forwarded-* headers so Traefik's forwardAuth passes a browser-reachable
    Location to the client.
    """
    cookie = request.cookies.get(COOKIE_NAME)
    username = _verify_cookie(cookie) if cookie else None
    if username:
        return Response(status_code=200, headers={"X-User-Email": username})

    # Build redirect URL from forwarded headers (set by Traefik)
    proto = request.headers.get("x-forwarded-proto", "http")
    host = request.headers.get("x-forwarded-host", request.headers.get("host", "localhost"))
    login_url = f"{proto}://{host}/login"
    return RedirectResponse(login_url, status_code=302)


@app.get("/health")
async def health():
    return {"status": "ok"}
