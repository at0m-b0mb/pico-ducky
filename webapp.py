# License : GPLv2.0
# Original copyright (c) 2023  Dave Bailey (dbisu, @daveisu)
# Enhanced web interface:
#   - Modern dark/light themed UI with theme persistence
#   - First-run setup wizard (no creds.py? -> /setup forced)
#   - HTTP Basic auth, CSRF tokens on POST, rate-limit + lockout
#   - Audit log with viewer & clear
#   - API token for /api/* endpoints
#   - Logout / panic wipe / duplicate / rename / download / upload
#   - Snippet library, server-side syntax highlighter, lint-on-save
#   - System page with reboot, free RAM, CPU temp, FS state, JSON /api/system

import gc
import os
import time
import storage
import asyncio
import binascii

import wsgiserver as server
from adafruit_wsgi.wsgi_app import WSGIApp
import wifi
import board

try:
    import microcontroller
except ImportError:
    microcontroller = None

from duckyinpython import *

# ---------------------------------------------------------------------------
# Optional credentials file. Created by the setup wizard on first run.
#   WEB_USERNAME = "admin"
#   WEB_PASSWORD = "..."
#   API_TOKEN    = "..."     # optional, gates /api/*
# ---------------------------------------------------------------------------
try:
    from creds import WEB_USERNAME, WEB_PASSWORD
except ImportError:
    WEB_USERNAME = ""
    WEB_PASSWORD = ""

try:
    from creds import API_TOKEN
except ImportError:
    API_TOKEN = ""

AUTH_ENABLED = bool(WEB_USERNAME) and bool(WEB_PASSWORD)
SETUP_REQUIRED = True  # re-checked below once file_exists() is defined


# ---------------------------------------------------------------------------
# Config / limits
# ---------------------------------------------------------------------------
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_FILENAME_LEN = 48
PAYLOAD_EXT = ".dd"
CREDS_PATH = "/creds.py"
AUDIT_LOG_PATH = "/audit.log"
AUDIT_LOG_MAX = 32 * 1024
ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)

BOOT_TIME = time.monotonic()

# Login rate limiting (global, since WSGI server doesn't reliably surface peer IP)
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 300
_failed_attempts = []

# CSRF token regenerated on every process start
def _make_csrf():
    try:
        return binascii.hexlify(os.urandom(16)).decode()
    except Exception:
        return "%x%x" % (int(time.monotonic() * 1000), id(object()))
CSRF_TOKEN = _make_csrf()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def file_exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False


# (Late init for SETUP_REQUIRED that referenced file_exists)
SETUP_REQUIRED = not file_exists(CREDS_PATH)


def html_escape(s):
    if s is None:
        return ""
    if not isinstance(s, str):
        s = str(s)
    return (s.replace("&", "&amp;")
             .replace("<", "&lt;")
             .replace(">", "&gt;")
             .replace('"', "&quot;")
             .replace("'", "&#39;"))


def py_escape(s):
    """Escape a string for embedding inside a Python double-quoted string literal."""
    return (s.replace("\\", "\\\\")
             .replace('"', '\\"')
             .replace("\n", "\\n")
             .replace("\r", "\\r"))


def format_size(n):
    if n is None:
        return "?"
    if n < 1024:
        return "%d B" % n
    if n < 1024 * 1024:
        return "%.1f KB" % (n / 1024)
    return "%.1f MB" % (n / (1024 * 1024))


def format_uptime(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    m, s = divmod(seconds, 60)
    if m < 60:
        return "%dm %ds" % (m, s)
    h, m = divmod(m, 60)
    if h < 24:
        return "%dh %dm" % (h, m)
    d, h = divmod(h, 24)
    return "%dd %dh" % (d, h)


def file_size(name):
    try:
        return os.stat(name)[6]
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# CSS / layout
# ---------------------------------------------------------------------------
BASE_CSS = """
:root{
 --bg:#0b1220;--panel:#111a2e;--panel-2:#172241;--border:#22304d;
 --muted:#7c8bab;--text:#e6edf7;--accent:#22d3ee;--accent-2:#7c3aed;
 --danger:#f43f5e;--ok:#10b981;--warn:#f59e0b;--input-bg:#0a1226;
 --radius:10px;--shadow:0 6px 16px rgba(0,0,0,.25);
}
html[data-theme="light"]{
 --bg:#f7fafc;--panel:#ffffff;--panel-2:#f1f5f9;--border:#e2e8f0;
 --muted:#64748b;--text:#0f172a;--accent:#0891b2;--accent-2:#7c3aed;
 --danger:#dc2626;--ok:#059669;--warn:#d97706;--input-bg:#ffffff;
 --shadow:0 6px 16px rgba(15,23,42,.08);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,sans-serif;
 font-size:15px;line-height:1.5;min-height:100vh;
 transition:background-color .25s,color .25s}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px}
header.app{display:flex;align-items:center;justify-content:space-between;
 padding:14px 20px;background:linear-gradient(135deg,var(--panel),var(--panel-2));
 border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10}
header.app .brand{display:flex;align-items:center;gap:10px;font-weight:700}
header.app .brand .logo{width:30px;height:30px;border-radius:8px;
 background:linear-gradient(135deg,var(--accent),var(--accent-2));
 display:inline-flex;align-items:center;justify-content:center;
 color:#06121f;font-weight:800;font-size:16px}
nav.app{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
nav.app a{padding:6px 12px;border-radius:999px;font-size:14px;color:var(--muted)}
nav.app a.active,nav.app a:hover{background:rgba(34,211,238,.12);
 color:var(--text);text-decoration:none}
.icon-btn{background:transparent;border:1px solid var(--border);color:var(--text);
 width:34px;height:34px;border-radius:999px;cursor:pointer;display:inline-flex;
 align-items:center;justify-content:center;font-size:14px;
 transition:background .15s,border-color .15s}
.icon-btn:hover{background:var(--panel-2);border-color:var(--accent)}
h1,h2,h3{margin:0 0 .5em 0}
h1{font-size:24px}
h2{font-size:18px;color:var(--text);font-weight:600}
h3{font-size:13px;color:var(--muted);font-weight:600;text-transform:uppercase;
 letter-spacing:.05em}
.card{background:var(--panel);border:1px solid var(--border);
 border-radius:var(--radius);padding:18px;margin-bottom:18px;
 box-shadow:var(--shadow)}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.spacer{flex:1}
.muted{color:var(--muted)}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;
 border:1px solid var(--border);background:var(--panel-2);color:var(--text);
 border-radius:8px;font-size:14px;cursor:pointer;text-decoration:none;
 font-family:inherit;
 transition:transform .06s,background .15s,border-color .15s}
.btn:hover{background:rgba(34,211,238,.08);border-color:var(--accent);text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn.primary{background:linear-gradient(135deg,var(--accent),#0891b2);
 color:#06121f;border-color:transparent;font-weight:600}
.btn.primary:hover{filter:brightness(1.08)}
.btn.danger{background:transparent;color:var(--danger);border-color:rgba(244,63,94,.4)}
.btn.danger:hover{background:rgba(244,63,94,.12)}
.btn.warn{background:transparent;color:var(--warn);border-color:rgba(245,158,11,.4)}
.btn.warn:hover{background:rgba(245,158,11,.12)}
.btn.ghost{background:transparent}
.btn.small{padding:4px 10px;font-size:13px}
.btn[disabled]{opacity:.5;cursor:not-allowed}
.input,textarea,select{
 width:100%;padding:10px 12px;background:var(--input-bg);color:var(--text);
 border:1px solid var(--border);border-radius:8px;font-size:14px;
 font-family:inherit;outline:none;transition:border-color .15s,box-shadow .15s}
.input:focus,textarea:focus{border-color:var(--accent);
 box-shadow:0 0 0 3px rgba(34,211,238,.12)}
textarea{resize:vertical;min-height:240px;line-height:1.55;tab-size:4;
 font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px}
label{display:block;margin-bottom:6px;font-size:13px;color:var(--muted)}
.field{margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th,td{padding:10px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
 color:var(--muted);font-weight:600}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(34,211,238,.04)}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;
 background:rgba(34,211,238,.1);color:var(--accent);
 border:1px solid rgba(34,211,238,.25)}
.alert{padding:12px 14px;border-radius:8px;margin-bottom:14px;
 border:1px solid var(--border)}
.alert.ok{background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.35);color:#10b981}
.alert.err{background:rgba(244,63,94,.1);border-color:rgba(244,63,94,.35);color:#f43f5e}
.alert.info{background:rgba(34,211,238,.08);border-color:rgba(34,211,238,.3);color:var(--accent)}
.alert.warn{background:rgba(245,158,11,.1);border-color:rgba(245,158,11,.35);color:#f59e0b}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.stat{background:var(--panel-2);border:1px solid var(--border);
 border-radius:8px;padding:14px}
.stat .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.stat .v{font-size:20px;font-weight:600;margin-top:4px;word-break:break-all}
.actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.actions form{margin:0}
.snippets{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.snip{padding:8px 12px;background:var(--panel-2);border:1px solid var(--border);
 border-radius:8px;cursor:pointer;font-family:ui-monospace,monospace;
 font-size:12px;text-align:left;color:var(--text);transition:.15s}
.snip:hover{border-color:var(--accent);color:var(--accent)}
footer.app{padding:20px;text-align:center;color:var(--muted);font-size:12px}
.kbd{font-family:ui-monospace,monospace;background:var(--input-bg);padding:1px 6px;
 border:1px solid var(--border);border-radius:4px;font-size:12px}
.empty{padding:40px 20px;text-align:center;color:var(--muted)}
.code-preview{background:var(--input-bg);padding:14px;border-radius:8px;
 border:1px solid var(--border);overflow:auto;
 font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;
 font-size:13px;line-height:1.55;white-space:pre;tab-size:4}
.tok-cmd{color:var(--accent);font-weight:600}
.tok-str{color:#a7f3d0}
html[data-theme="light"] .tok-str{color:#047857}
.tok-num{color:#fbbf24}
.tok-com{color:var(--muted);font-style:italic}
.tok-var{color:#c4b5fd}
html[data-theme="light"] .tok-var{color:#6d28d9}
.tok-op{color:#f472b6}
.audit{font-family:ui-monospace,monospace;font-size:12px;background:var(--input-bg);
 padding:10px;border:1px solid var(--border);border-radius:8px;
 max-height:60vh;overflow:auto;white-space:pre-wrap;word-break:break-all}
.warn-list{margin:8px 0 0 0;padding-left:18px}
.warn-list li{margin:4px 0;font-size:13px}
.shortcut{display:inline-flex;gap:4px;align-items:center;color:var(--muted);
 font-size:12px;margin-left:8px}
@media(max-width:600px){
 header.app{flex-direction:column;gap:10px;align-items:flex-start}
 .wrap{padding:16px 10px}
 th.hide-sm,td.hide-sm{display:none}
}
"""

THEME_BOOT_JS = (
    "(function(){var t=localStorage.getItem('pdtheme')||'dark';"
    "document.documentElement.setAttribute('data-theme',t)})();"
)


def nav_for(active):
    items = [
        ("home", "/", "Payloads"),
        ("new", "/new", "New"),
        ("upload", "/upload", "Upload"),
        ("snippets", "/snippets", "Snippets"),
        ("audit", "/audit", "Audit"),
        ("system", "/system", "System"),
    ]
    out = ""
    for k, href, label in items:
        cls = ' class="active"' if k == active else ""
        out += '<a href="%s"%s>%s</a>' % (href, cls, label)
    # Theme + logout buttons
    out += (
        '<button type="button" class="icon-btn" id="themeBtn" '
        'title="Toggle theme" onclick="(function(){var d=document.documentElement,'
        't=d.getAttribute(\'data-theme\')==\'light\'?\'dark\':\'light\';'
        'd.setAttribute(\'data-theme\',t);localStorage.setItem(\'pdtheme\',t)})()">'
        '&#9788;</button>'
    )
    if AUTH_ENABLED:
        out += (
            '<a class="icon-btn" href="/logout" '
            'title="Sign out">&#128274;</a>'
        )
    return out


def layout(title, body, active="home", flash=None):
    flash_html = ""
    if flash:
        kind, msg = flash
        flash_html = '<div class="alert %s">%s</div>' % (
            kind, html_escape(msg))
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8"><title>%s &middot; Pico Ducky</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>%s</style>'
        '<script>%s</script>'
        '</head><body>'
        '<header class="app"><div class="brand">'
        '<span class="logo">D</span>Pico Ducky</div>'
        '<nav class="app">%s</nav></header>'
        '<div class="wrap">%s%s</div>'
        '<footer class="app">pico-ducky &middot; secured by you &middot; '
        '<a href="/system">system</a>'
        '</footer></body></html>'
    ) % (html_escape(title), BASE_CSS, THEME_BOOT_JS,
         nav_for(active), flash_html, body)


# ---------------------------------------------------------------------------
# Request / form helpers
# ---------------------------------------------------------------------------
_hexdig = '0123456789ABCDEFabcdef'


def cleanup_text(s):
    if s is None:
        return ""
    if isinstance(s, (bytes, bytearray)):
        try:
            s = s.decode('utf-8')
        except UnicodeError:
            s = s.decode('latin-1')
    out = []
    i = 0
    n = len(s)
    while i < n:
        c = s[i]
        if c == '+':
            out.append(' ')
            i += 1
        elif c == '%' and i + 2 < n and s[i + 1] in _hexdig and s[i + 2] in _hexdig:
            try:
                out.append(chr(int(s[i + 1:i + 3], 16)))
                i += 3
            except ValueError:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return ''.join(out)


def parse_form(raw):
    if raw is None:
        return {}
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode('utf-8')
        except UnicodeError:
            raw = raw.decode('latin-1')
    form = {}
    for pair in raw.split('&'):
        if not pair:
            continue
        if '=' in pair:
            k, v = pair.split('=', 1)
        else:
            k, v = pair, ''
        form[cleanup_text(k)] = cleanup_text(v)
    return form


def request_body(request):
    try:
        raw = request.body.getvalue()
    except AttributeError:
        raw = ''
    if len(raw) > MAX_PAYLOAD_BYTES * 2:
        raise ValueError("body too large")
    return raw


def get_header(request, name):
    keys = ('HTTP_' + name.upper().replace('-', '_'), name)
    for src_name in ('environ', 'headers'):
        src = getattr(request, src_name, None)
        if not src:
            continue
        for key in keys:
            try:
                v = src.get(key, '') if hasattr(src, 'get') else getattr(src, key, '')
                if v:
                    return v
            except Exception:
                pass
    return ''


def query_string(request):
    env = getattr(request, 'environ', None)
    if env is not None:
        try:
            qs = env.get('QUERY_STRING', '') or ''
            if qs:
                return qs
        except AttributeError:
            qs = getattr(env, 'QUERY_STRING', '') or ''
            if qs:
                return qs
    qp = getattr(request, 'query_params', None)
    if isinstance(qp, str):
        return qp
    path = getattr(request, 'path', '') or ''
    if '?' in path:
        return path.split('?', 1)[1]
    return ''


def query_get(request, key):
    qs = query_string(request)
    if not qs:
        return None
    for pair in qs.split('&'):
        if '=' in pair:
            k, v = pair.split('=', 1)
            if k == key:
                return cleanup_text(v)
        elif pair == key:
            return ''
    return None


# ---------------------------------------------------------------------------
# Auth + rate limiting
# ---------------------------------------------------------------------------
def _b64decode(s):
    try:
        return binascii.a2b_base64(s).decode('utf-8', 'replace')
    except Exception:
        return ''


def _ct_equals(a, b):
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0


def _prune_failed():
    now = time.monotonic()
    cutoff = now - LOCKOUT_SECONDS
    _failed_attempts[:] = [t for t in _failed_attempts if t > cutoff]


def is_locked_out():
    _prune_failed()
    return len(_failed_attempts) >= LOCKOUT_THRESHOLD


def lockout_remaining():
    if not _failed_attempts:
        return 0
    return int(LOCKOUT_SECONDS - (time.monotonic() - _failed_attempts[0]))


def record_failed_attempt():
    _failed_attempts.append(time.monotonic())
    audit("auth.fail", "%d/%d" % (len(_failed_attempts), LOCKOUT_THRESHOLD))


def clear_failed_attempts():
    _failed_attempts[:] = []


def is_authenticated(request):
    if not AUTH_ENABLED:
        return True
    if is_locked_out():
        return False
    auth = get_header(request, 'Authorization')
    if not auth or not auth.startswith('Basic '):
        return False
    decoded = _b64decode(auth[6:].strip())
    if ':' not in decoded:
        return False
    u, p = decoded.split(':', 1)
    ok = _ct_equals(u, WEB_USERNAME) and _ct_equals(p, WEB_PASSWORD)
    if ok:
        clear_failed_attempts()
    return ok


def api_authenticated(request):
    """API auth: bearer token OR ?token=... OR fall back to basic auth."""
    if API_TOKEN:
        auth = get_header(request, 'Authorization')
        if auth.startswith('Bearer '):
            if _ct_equals(auth[7:].strip(), API_TOKEN):
                return True
        t = query_get(request, 'token') or ''
        if t and _ct_equals(t, API_TOKEN):
            return True
        return False
    return is_authenticated(request)


def unauthorized_response(reason=""):
    msg = reason or "Sign in with the credentials you set on the device."
    body = layout("Sign in",
                  '<div class="card"><h1>Authentication required</h1>'
                  '<p class="muted">%s</p></div>' % html_escape(msg))
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky"')],
            body)


def locked_response():
    body = layout("Locked out",
                  '<div class="card"><h1>Too many failed attempts</h1>'
                  '<p class="muted">Try again in ~%d seconds. '
                  'Power-cycle the Pico to reset immediately.</p>'
                  '</div>' % lockout_remaining())
    return ("429 Too Many Requests",
            [("Content-Type", "text/html; charset=utf-8"),
             ("Retry-After", str(LOCKOUT_SECONDS))],
            body)


# ---------------------------------------------------------------------------
# CSRF
# ---------------------------------------------------------------------------
def csrf_field():
    return '<input type="hidden" name="_csrf" value="%s">' % CSRF_TOKEN


def csrf_ok(form):
    token = form.get('_csrf', '')
    if not token:
        return False
    return _ct_equals(token, CSRF_TOKEN)


def csrf_failure_response():
    body = layout("Bad request",
                  '<div class="card"><h1>Security check failed</h1>'
                  '<p class="muted">CSRF token missing or invalid. '
                  'Reload the page and try again.</p>'
                  '<p><a class="btn" href="/">Back</a></p></div>')
    return ("403 Forbidden",
            [("Content-Type", "text/html; charset=utf-8")], body)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
SEC_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy",
     "default-src 'self'; "
     "style-src 'self' 'unsafe-inline'; "
     "script-src 'self' 'unsafe-inline'; "
     "img-src 'self' data:; "
     "form-action 'self'; "
     "frame-ancestors 'none'; "
     "base-uri 'none'"),
    ("Permissions-Policy", "geolocation=(), camera=(), microphone=()"),
)


def html_response(html, status="200 OK", extra_headers=()):
    headers = [("Content-Type", "text/html; charset=utf-8")]
    headers.extend(SEC_HEADERS)
    headers.extend(extra_headers)
    return (status, headers, html)


def text_response(text, status="200 OK", filename=None):
    headers = [("Content-Type", "text/plain; charset=utf-8")]
    headers.extend(SEC_HEADERS)
    if filename:
        headers.append(("Content-Disposition",
                        'attachment; filename="%s"' % filename))
    return (status, headers, text)


def json_response(payload, status="200 OK"):
    headers = [("Content-Type", "application/json; charset=utf-8")]
    headers.extend(SEC_HEADERS)
    return (status, headers, _to_json(payload))


def redirect(location, flash=None):
    loc = location
    if flash:
        sep = "&" if "?" in loc else "?"
        loc = "%s%sm=%s" % (loc, sep, flash)
    return ("303 See Other",
            [("Location", loc),
             ("Content-Type", "text/html; charset=utf-8"),
             ("Cache-Control", "no-store")],
            '<a href="%s">Redirecting...</a>' % html_escape(loc))


# Minimal JSON encoder (avoids importing the `json` module on CircuitPython,
# which is present but costs RAM)
def _to_json(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return '"' + (v.replace("\\", "\\\\").replace('"', '\\"')
                       .replace("\n", "\\n").replace("\r", "\\r")
                       .replace("\t", "\\t")) + '"'
    if isinstance(v, (list, tuple)):
        return "[" + ",".join(_to_json(x) for x in v) + "]"
    if isinstance(v, dict):
        return "{" + ",".join('"%s":%s' % (k, _to_json(val))
                              for k, val in v.items()) + "}"
    return _to_json(str(v))


# ---------------------------------------------------------------------------
# Filename validation
# ---------------------------------------------------------------------------
def safe_filename(name):
    if not name:
        return None
    name = name.strip()
    if not name or len(name) > MAX_FILENAME_LEN:
        return None
    if "/" in name or "\\" in name or ".." in name or name.startswith("."):
        return None
    for ch in name:
        if ch not in ALLOWED_NAME_CHARS:
            return None
    if not name.endswith(PAYLOAD_EXT):
        return None
    return name


def normalize_payload_name(name):
    if not name:
        return None
    name = name.strip()
    if not name:
        return None
    if not name.endswith(PAYLOAD_EXT):
        name = name + PAYLOAD_EXT
    return safe_filename(name)


def list_payloads():
    out = []
    try:
        for f in os.listdir("/"):
            if f.endswith(PAYLOAD_EXT):
                out.append((f, file_size(f)))
    except OSError:
        pass
    out.sort()
    return out


# ---------------------------------------------------------------------------
# Filesystem write helpers (always restore RO)
# ---------------------------------------------------------------------------
def _remount_rw():
    storage.remount("/", readonly=False)


def _remount_ro():
    try:
        storage.remount("/", readonly=True)
    except Exception:
        pass


def write_file_safely(name, contents):
    if len(contents) > MAX_PAYLOAD_BYTES and not name.startswith("/audit") and name != CREDS_PATH:
        raise ValueError("payload exceeds %d bytes" % MAX_PAYLOAD_BYTES)
    try:
        _remount_rw()
        with open(name, "w") as f:
            f.write(contents)
    finally:
        _remount_ro()


def delete_file_safely(name):
    try:
        _remount_rw()
        os.remove(name)
    finally:
        _remount_ro()


def rename_file_safely(old, new):
    try:
        _remount_rw()
        os.rename(old, new)
    finally:
        _remount_ro()


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------
def audit(action, details=""):
    line = "[%.1f] %s %s\n" % (time.monotonic(), action, details or "")
    try:
        try:
            sz = file_size(AUDIT_LOG_PATH)
        except Exception:
            sz = 0
        try:
            _remount_rw()
            if sz + len(line) > AUDIT_LOG_MAX:
                # Truncate: keep tail of half the cap
                try:
                    with open(AUDIT_LOG_PATH, "r") as f:
                        text = f.read()
                    text = text[-(AUDIT_LOG_MAX // 2):]
                except Exception:
                    text = ""
                with open(AUDIT_LOG_PATH, "w") as f:
                    f.write(text)
            with open(AUDIT_LOG_PATH, "a") as f:
                f.write(line)
        finally:
            _remount_ro()
    except Exception as ex:
        print("audit failed:", ex)


def read_audit_tail(max_bytes=12 * 1024):
    if not file_exists(AUDIT_LOG_PATH):
        return ""
    try:
        with open(AUDIT_LOG_PATH, "r") as f:
            text = f.read()
        return text[-max_bytes:]
    except Exception:
        return ""


def clear_audit_log():
    try:
        _remount_rw()
        with open(AUDIT_LOG_PATH, "w") as f:
            f.write("")
    finally:
        _remount_ro()


# ---------------------------------------------------------------------------
# Credentials persistence (first-run setup wizard)
# ---------------------------------------------------------------------------
def apply_credentials(username, password, api_token=""):
    global AUTH_ENABLED, WEB_USERNAME, WEB_PASSWORD, API_TOKEN, SETUP_REQUIRED
    WEB_USERNAME = username
    WEB_PASSWORD = password
    API_TOKEN = api_token or ""
    AUTH_ENABLED = bool(username and password)
    lines = [
        '# Generated by the Pico Ducky setup wizard.',
        'WEB_USERNAME = "%s"' % py_escape(username),
        'WEB_PASSWORD = "%s"' % py_escape(password),
    ]
    if API_TOKEN:
        lines.append('API_TOKEN = "%s"' % py_escape(API_TOKEN))
    write_file_safely(CREDS_PATH, "\n".join(lines) + "\n")
    SETUP_REQUIRED = False


# ---------------------------------------------------------------------------
# DuckyScript lightweight linter / highlighter
# ---------------------------------------------------------------------------
KNOWN_COMMANDS = frozenset([
    "REM", "REM_BLOCK", "END_REM",
    "DEFAULT_DELAY", "DEFAULTDELAY", "DELAY",
    "STRING", "STRINGLN", "PRINTSTRING", "STRING_DELAY",
    "ENTER", "TAB", "ESCAPE", "ESC", "BACKSPACE", "SPACE", "DELETE",
    "INSERT", "HOME", "END", "PAGEUP", "PAGEDOWN", "CAPSLOCK",
    "PRINTSCREEN", "SCROLLLOCK", "PAUSE", "BREAK", "NUMLOCK",
    "UP", "DOWN", "LEFT", "RIGHT", "UPARROW", "DOWNARROW",
    "LEFTARROW", "RIGHTARROW",
    "GUI", "WINDOWS", "RWINDOWS", "RGUI", "COMMAND", "RCOMMAND",
    "ALT", "RALT", "OPTION", "ROPTION",
    "CTRL", "CONTROL", "RCTRL", "SHIFT", "RSHIFT",
    "APP", "MENU",
    "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10",
    "F11", "F12", "F13", "F14", "F15", "F16", "F17", "F18", "F19",
    "F20", "F21", "F22", "F23", "F24",
    "MK_VOLUP", "MK_VOLDOWN", "MK_MUTE", "MK_NEXT", "MK_PREV",
    "MK_PP", "MK_STOP",
    "REPEAT", "WAIT_FOR_BUTTON_PRESS", "WAIT_FOR_CAPS_ON", "WAIT_FOR_CAPS_OFF",
    "WAIT_FOR_NUM_ON", "WAIT_FOR_NUM_OFF",
    "VAR", "IF", "ELSE", "END_IF", "WHILE", "END_WHILE",
    "FUNCTION", "END_FUNCTION", "RETURN",
    "HOLD", "RELEASE",
    "INJECT_MOD", "RESTORE_MOD", "SAVE_HOST_KEYBOARD_LOCK_STATE",
    "RESTORE_HOST_KEYBOARD_LOCK_STATE",
])

CMD_NEEDS_ARG = frozenset([
    "STRING", "STRINGLN", "PRINTSTRING", "DELAY", "DEFAULT_DELAY",
    "DEFAULTDELAY", "STRING_DELAY", "REPEAT",
])


def lint_payload(text):
    warnings = []
    if not text:
        warnings.append("Payload is empty.")
        return warnings
    lines = text.splitlines()
    consec_delays = 0
    for idx, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            consec_delays = 0
            continue
        # Variable assignment lines look like  $X = ...
        if line.startswith("$") or line.startswith("REM"):
            consec_delays = 0
            continue
        parts = line.split(None, 1)
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("DELAY",):
            consec_delays += 1
            if consec_delays >= 3:
                warnings.append("Line %d: many consecutive DELAYs; consider DEFAULT_DELAY." % idx)
        else:
            consec_delays = 0
        if cmd not in KNOWN_COMMANDS:
            # ignore lines that are obviously continuation of multi-line constructs
            warnings.append("Line %d: unknown command '%s'. Typo?" % (idx, cmd))
        if cmd in CMD_NEEDS_ARG and not arg:
            warnings.append("Line %d: '%s' is missing an argument." % (idx, cmd))
        if cmd in ("DELAY", "DEFAULT_DELAY", "DEFAULTDELAY", "STRING_DELAY") and arg:
            try:
                v = int(arg.split()[0])
                if v < 0:
                    warnings.append("Line %d: '%s' value should be non-negative." % (idx, cmd))
            except ValueError:
                # Could be a variable reference like $VAR — allow it
                if not arg.lstrip().startswith("$"):
                    warnings.append("Line %d: '%s' expects an integer." % (idx, cmd))
    if len(warnings) > 30:
        warnings = warnings[:30] + ["...and %d more." % (len(warnings) - 30)]
    return warnings


def highlight_payload(text):
    out = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        leading = raw[:len(raw) - len(stripped)]
        out.append(html_escape(leading))
        if not stripped:
            out.append("\n")
            continue
        upper = stripped.upper()
        if upper.startswith("REM"):
            out.append('<span class="tok-com">%s</span>\n' % html_escape(stripped))
            continue
        parts = stripped.split(None, 1)
        cmd = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        # Command token
        out.append('<span class="tok-cmd">%s</span>' % html_escape(cmd))
        if rest:
            out.append(" ")
            cmd_u = cmd.upper()
            if cmd_u in ("STRING", "STRINGLN", "PRINTSTRING"):
                out.append('<span class="tok-str">%s</span>'
                           % html_escape(rest))
            else:
                # Highlight $variables and numbers within rest
                buf = ""
                i = 0
                while i < len(rest):
                    c = rest[i]
                    if c == '$':
                        # Flush buf
                        if buf:
                            out.append(html_escape(buf))
                            buf = ""
                        j = i + 1
                        while j < len(rest) and (rest[j].isalpha() or rest[j].isdigit() or rest[j] == '_'):
                            j += 1
                        out.append('<span class="tok-var">%s</span>'
                                   % html_escape(rest[i:j]))
                        i = j
                    elif c.isdigit():
                        if buf:
                            out.append(html_escape(buf))
                            buf = ""
                        j = i
                        while j < len(rest) and rest[j].isdigit():
                            j += 1
                        out.append('<span class="tok-num">%s</span>'
                                   % html_escape(rest[i:j]))
                        i = j
                    elif c in "=+-*/<>!&|()":
                        if buf:
                            out.append(html_escape(buf))
                            buf = ""
                        out.append('<span class="tok-op">%s</span>'
                                   % html_escape(c))
                        i += 1
                    else:
                        buf += c
                        i += 1
                if buf:
                    out.append(html_escape(buf))
        out.append("\n")
    return ''.join(out)


# ---------------------------------------------------------------------------
# Flash messages (encoded in URL ?m=KEY)
# ---------------------------------------------------------------------------
FLASH_MESSAGES = {
    "saved": ("ok", "Payload saved."),
    "deleted": ("ok", "Payload deleted."),
    "renamed": ("ok", "Payload renamed."),
    "uploaded": ("ok", "Payload uploaded."),
    "ran": ("ok", "Payload executed."),
    "duplicated": ("ok", "Payload duplicated."),
    "wiped": ("ok", "All payloads wiped."),
    "logs_cleared": ("ok", "Audit log cleared."),
    "setup_done": ("ok", "Setup complete. Sign in to continue."),
    "creds_updated": ("ok", "Credentials updated."),
    "bad_name": ("err", "Invalid filename. Use A-Z, 0-9, _, - and end in .dd"),
    "too_big": ("err", "Payload too large."),
    "not_found": ("err", "Payload not found."),
    "exists": ("err", "A payload with that name already exists."),
    "bad_request": ("err", "Bad request."),
    "weak_pw": ("err", "Password must be at least 8 characters."),
    "pw_mismatch": ("err", "Passwords do not match."),
}


def get_flash(request):
    key = query_get(request, 'm')
    if key:
        return FLASH_MESSAGES.get(key)
    return None


# ---------------------------------------------------------------------------
# Snippet library
# ---------------------------------------------------------------------------
SNIPPETS = [
    ("Comment",          "REM This is a comment"),
    ("Delay (ms)",       "DELAY 500"),
    ("Default delay",    "DEFAULT_DELAY 100"),
    ("Type string",      "STRING Hello, world!"),
    ("Enter",            "ENTER"),
    ("Tab",              "TAB"),
    ("Escape",           "ESCAPE"),
    ("Backspace",        "BACKSPACE"),
    ("GUI (Win/Cmd)",    "GUI r"),
    ("Alt+F4",           "ALT F4"),
    ("Ctrl+Alt+Del",     "CTRL ALT DELETE"),
    ("Ctrl+Shift+Esc",   "CTRL SHIFT ESCAPE"),
    ("Open Run dialog",  "GUI r\nDELAY 200\nSTRING cmd\nENTER"),
    ("Open PowerShell",  "GUI r\nDELAY 200\nSTRING powershell\nENTER"),
    ("WHILE loop",       "VAR $I = 0\nWHILE ( $I < 5 )\n  STRING tick\n  ENTER\n  $I = $I + 1\nEND_WHILE"),
    ("IF/ELSE",          "IF ( $_CAPSLOCK_ON == TRUE ) THEN\n  STRING caps on\nELSE\n  STRING caps off\nEND_IF"),
    ("Random number",    "$_RANDOM_MIN = 1\n$_RANDOM_MAX = 100\nSTRINGLN $_RANDOM_INT"),
    ("Exfil mode",       "REM $_EXFIL_MODE_ENABLED = TRUE"),
]


# ---------------------------------------------------------------------------
# Page renderers
# ---------------------------------------------------------------------------
def render_setup(flash=None, prefill_user=""):
    body = (
        '<div class="card"><h1>First-time setup</h1>'
        '<p class="muted">Set credentials for the web UI. This file is stored on '
        'the device as <span class="kbd">creds.py</span> — keep it secret.</p>'
        '<form method="post" action="/setup">'
        '<input type="hidden" name="_csrf" value="%s">'
        '<div class="field"><label>Username</label>'
        '<input class="input" name="username" minlength="3" maxlength="32" '
        'required autocomplete="username" value="%s"></div>'
        '<div class="field"><label>Password (min 8 chars)</label>'
        '<input class="input" type="password" name="password" minlength="8" '
        'maxlength="64" required autocomplete="new-password"></div>'
        '<div class="field"><label>Confirm password</label>'
        '<input class="input" type="password" name="password2" minlength="8" '
        'maxlength="64" required autocomplete="new-password"></div>'
        '<div class="field"><label>API token (optional, for /api/* automation)</label>'
        '<input class="input" name="api_token" maxlength="64" '
        'placeholder="leave blank to disable"></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Save and lock down</button>'
        '</div></form></div>'
    ) % (CSRF_TOKEN, html_escape(prefill_user))
    return layout("Setup", body, active="home", flash=flash)


def render_home(flash=None):
    payloads = list_payloads()
    if not payloads:
        body = (
            '<div class="card"><h1>No payloads yet</h1>'
            '<p class="muted">Create your first script or upload one to get started.</p>'
            '<div class="row" style="margin-top:12px">'
            '<a class="btn primary" href="/new">+ New Script</a> '
            '<a class="btn" href="/upload">Upload</a> '
            '<a class="btn" href="/snippets">Browse Snippets</a>'
            '</div></div>'
        )
        return layout("Payloads", body, active="home", flash=flash)

    rows = []
    for name, size in payloads:
        safe = html_escape(name)
        rows.append(
            '<tr>'
            '<td><strong>%s</strong></td>'
            '<td class="hide-sm muted">%s</td>'
            '<td><div class="actions">'
            '<a class="btn small" href="/edit/%s">Edit</a>'
            '<a class="btn small ghost" href="/preview/%s">Preview</a>'
            '<a class="btn small ghost" href="/download/%s">Download</a>'
            '<form method="post" action="/duplicate/%s" style="display:inline">'
            '%s<button class="btn small ghost" type="submit">Clone</button></form>'
            '<form method="post" action="/run/%s" style="display:inline">'
            '%s<button class="btn small warn" type="submit" '
            'onclick="return confirm(\'Run %s? This will inject keystrokes.\')">'
            'Run</button></form>'
            '<form method="post" action="/delete/%s" style="display:inline">'
            '%s<button class="btn small danger" type="submit" '
            'onclick="return confirm(\'Delete %s permanently?\')">'
            'Delete</button></form>'
            '</div></td>'
            '</tr>'
            % (safe, format_size(size), safe, safe, safe,
               safe, csrf_field(),
               safe, csrf_field(), safe,
               safe, csrf_field(), safe)
        )

    body = (
        '<div class="card">'
        '<div class="row"><h1>Payloads</h1>'
        '<span class="badge">%d</span><div class="spacer"></div>'
        '<a class="btn primary" href="/new">+ New</a>'
        '<a class="btn" href="/upload">Upload</a>'
        '<form method="post" action="/wipe" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'WIPE ALL %d payloads? This cannot be undone.\')'
        '&amp;&amp;confirm(\'Are you absolutely sure?\')">'
        'Wipe all</button></form>'
        '</div>'
        '<div class="field" style="margin-top:14px">'
        '<input id="filter" class="input" placeholder="Filter payloads...">'
        '</div>'
        '<table><thead><tr><th>Name</th>'
        '<th class="hide-sm">Size</th>'
        '<th style="text-align:right">Actions</th></tr></thead>'
        '<tbody id="rows">%s</tbody></table></div>'
        '<script>'
        'var f=document.getElementById("filter"),'
        'rs=document.querySelectorAll("#rows tr");'
        'f.addEventListener("input",function(){'
        'var q=f.value.toLowerCase();'
        'for(var i=0;i<rs.length;i++){'
        'rs[i].style.display=rs[i].innerText.toLowerCase().indexOf(q)>-1?"":"none"'
        '}});'
        '</script>'
    ) % (len(payloads), csrf_field(), len(payloads), ''.join(rows))
    return layout("Payloads", body, active="home", flash=flash)


def render_editor(filename, contents, flash=None, warnings=None):
    safe = html_escape(filename)
    snip_buttons = ''.join(
        '<button type="button" class="snip" data-snip="%s">%s</button>'
        % (html_escape(code).replace('\n', '\\n'), html_escape(label))
        for label, code in SNIPPETS
    )
    warn_html = ""
    if warnings:
        items = ''.join('<li>%s</li>' % html_escape(w) for w in warnings)
        warn_html = ('<div class="alert warn"><strong>Lint warnings</strong>'
                     '<ul class="warn-list">%s</ul></div>' % items)
    body = (
        '<div class="card"><div class="row">'
        '<h1>Editing %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/preview/%s">Preview</a>'
        '<a class="btn" href="/">Cancel</a></div>'
        '%s'
        '<form method="post" action="/write/%s" id="ed">'
        '%s'
        '<div class="field">'
        '<label>Script <span class="shortcut"><span class="kbd">Ctrl</span>+<span class="kbd">S</span> to save</span></label>'
        '<textarea name="scriptData" id="ta" data-name="%s">%s</textarea>'
        '<p class="muted" id="stats" style="margin:6px 0 0 0;font-size:12px">'
        'Max %s. Tip: click a snippet below to insert.</p>'
        '</div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Save</button>'
        '<a class="btn" href="/download/%s">Download</a>'
        '<a class="btn" href="/rename/%s">Rename</a>'
        '<button type="button" class="btn ghost" id="restoreBtn" style="display:none">'
        'Restore unsaved draft</button>'
        '</div></form></div>'
        '<div class="card"><h3>Snippets</h3>'
        '<div class="snippets">%s</div></div>'
        '<script>'
        'var ta=document.getElementById("ta"),st=document.getElementById("stats"),'
        'rb=document.getElementById("restoreBtn"),'
        'dk="pd:draft:"+ta.dataset.name;'
        'function upd(){var l=ta.value.split("\\n").length,b=ta.value.length;'
        'st.innerText=l+" lines \\u00B7 "+b+" bytes";try{localStorage.setItem(dk,ta.value)}catch(e){}}'
        'ta.addEventListener("input",upd);upd();'
        'try{var d=localStorage.getItem(dk);'
        'if(d&&d!==ta.value){rb.style.display="";'
        'rb.addEventListener("click",function(){ta.value=d;upd();rb.style.display="none"})}}catch(e){}'
        'document.querySelectorAll(".snip").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'var s=b.getAttribute("data-snip").replace(/\\\\n/g,"\\n");'
        'var p=ta.selectionStart||0;'
        'ta.value=ta.value.slice(0,p)+s+"\\n"+ta.value.slice(p);'
        'ta.focus();upd();'
        '})});'
        'document.addEventListener("keydown",function(e){'
        'if((e.ctrlKey||e.metaKey)&&e.key==="s"){'
        'e.preventDefault();document.getElementById("ed").submit()}});'
        'document.getElementById("ed").addEventListener("submit",function(){'
        'try{localStorage.removeItem(dk)}catch(e){}});'
        '</script>'
    ) % (safe, safe, warn_html, safe, csrf_field(), safe,
         html_escape(contents),
         format_size(MAX_PAYLOAD_BYTES), safe, safe, snip_buttons)
    return layout("Edit " + filename, body, active="home", flash=flash)


def render_new(flash=None, prefill_name="", prefill_data=""):
    body = (
        '<div class="card"><h1>New Script</h1>'
        '<form method="post" action="/new">'
        '%s'
        '<div class="field"><label>Filename</label>'
        '<input class="input" name="scriptName" '
        'placeholder="my_payload.dd" value="%s" required></div>'
        '<div class="field"><label>Script</label>'
        '<textarea name="scriptData" placeholder="REM Your DuckyScript here">'
        '%s</textarea></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Create</button>'
        '<a class="btn" href="/">Cancel</a>'
        '<a class="btn ghost" href="/snippets">Browse snippets</a>'
        '</div></form></div>'
    ) % (csrf_field(), html_escape(prefill_name), html_escape(prefill_data))
    return layout("New script", body, active="new", flash=flash)


def render_upload(flash=None):
    body = (
        '<div class="card"><h1>Upload Payload</h1>'
        '<p class="muted">Select a local <span class="kbd">.dd</span> file. '
        'It is read in your browser and posted as text to the device.</p>'
        '<div class="field">'
        '<input type="file" id="file" accept=".dd,.txt,text/plain" class="input">'
        '</div>'
        '<div class="field"><label>Save as</label>'
        '<input class="input" id="name" placeholder="payload.dd"></div>'
        '<form method="post" action="/new" id="upf">'
        '%s'
        '<input type="hidden" name="scriptName" id="hname">'
        '<textarea name="scriptData" id="hdata" style="display:none"></textarea>'
        '<div class="row">'
        '<button class="btn primary" type="submit" id="go" disabled>Upload</button>'
        '<a class="btn" href="/">Cancel</a></div></form></div>'
        '<script>'
        'var fi=document.getElementById("file"),na=document.getElementById("name"),'
        'hn=document.getElementById("hname"),hd=document.getElementById("hdata"),'
        'go=document.getElementById("go");'
        'function ck(){go.disabled=!(fi.files[0]&&(na.value||fi.files[0].name))}'
        'fi.addEventListener("change",function(){'
        'if(!na.value&&fi.files[0])na.value=fi.files[0].name;ck();'
        'var r=new FileReader();r.onload=function(){hd.value=r.result};'
        'r.readAsText(fi.files[0])});'
        'na.addEventListener("input",ck);'
        'document.getElementById("upf").addEventListener("submit",function(){'
        'hn.value=na.value});'
        '</script>'
    ) % csrf_field()
    return layout("Upload", body, active="upload", flash=flash)


def render_snippets(flash=None):
    items = ''
    for label, code in SNIPPETS:
        items += (
            '<div class="card"><div class="row">'
            '<h2>%s</h2><div class="spacer"></div>'
            '<button class="btn small" onclick="copyText(this)">Copy</button>'
            '</div>'
            '<pre class="code-preview" style="margin:8px 0 0">'
            '<code>%s</code></pre></div>'
        ) % (html_escape(label), html_escape(code))
    body = (
        '<div class="card"><h1>DuckyScript Snippets</h1>'
        '<p class="muted">Common building blocks. Click <em>Copy</em> or '
        'use them from inside the editor sidebar.</p></div>%s'
        '<script>'
        'function copyText(b){var c=b.parentElement.parentElement'
        '.querySelector("code").innerText;'
        'if(navigator.clipboard){navigator.clipboard.writeText(c);'
        'b.innerText="Copied"}else{b.innerText="Select manually"}}'
        '</script>'
    ) % items
    return layout("Snippets", body, active="snippets", flash=flash)


def render_preview(filename, contents):
    safe = html_escape(filename)
    body = (
        '<div class="card"><div class="row">'
        '<h1>Preview: %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/edit/%s">Edit</a>'
        '<a class="btn" href="/">Back</a></div>'
        '<pre class="code-preview">%s</pre></div>'
    ) % (safe, safe, highlight_payload(contents))
    return layout("Preview " + filename, body, active="home")


def render_rename(filename, flash=None):
    safe = html_escape(filename)
    body = (
        '<div class="card"><h1>Rename %s</h1>'
        '<form method="post" action="/rename/%s">'
        '%s'
        '<div class="field"><label>New name</label>'
        '<input class="input" name="newName" value="%s" required></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Rename</button>'
        '<a class="btn" href="/">Cancel</a></div></form></div>'
    ) % (safe, safe, csrf_field(), safe)
    return layout("Rename " + filename, body, active="home", flash=flash)


def render_audit(flash=None):
    tail = read_audit_tail()
    if not tail:
        tail = "(empty)"
    body = (
        '<div class="card"><div class="row">'
        '<h1>Audit Log</h1><div class="spacer"></div>'
        '<form method="post" action="/audit/clear" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Clear audit log?\')">Clear log</button>'
        '</form></div>'
        '<p class="muted">Most recent entries (truncated to last 12 KB). '
        'Times are seconds since boot.</p>'
        '<pre class="audit">%s</pre></div>'
    ) % (csrf_field(), html_escape(tail))
    return layout("Audit", body, active="audit", flash=flash)


def _system_facts():
    gc.collect()
    free = gc.mem_free() if hasattr(gc, 'mem_free') else None
    alloc = gc.mem_alloc() if hasattr(gc, 'mem_alloc') else None
    try:
        ap_ip = repr(wifi.radio.ipv4_address_ap)
    except Exception:
        ap_ip = "?"
    cpu_temp = None
    if microcontroller and hasattr(microcontroller, 'cpu'):
        try:
            cpu_temp = float(microcontroller.cpu.temperature)
        except Exception:
            pass
    try:
        ro = storage.getmount('/').readonly
        fs = "read-only" if ro else "writable"
    except Exception:
        fs = "?"
    try:
        stations = len(wifi.radio.stations_ap)
    except Exception:
        stations = None
    return {
        "board": getattr(board, 'board_id', '?'),
        "ap_ip": ap_ip,
        "uptime_s": int(time.monotonic() - BOOT_TIME),
        "uptime": format_uptime(time.monotonic() - BOOT_TIME),
        "free_mem": free,
        "used_mem": alloc,
        "cpu_temp_c": cpu_temp,
        "fs": fs,
        "auth_enabled": AUTH_ENABLED,
        "api_token_set": bool(API_TOKEN),
        "payload_count": len(list_payloads()),
        "stations_connected": stations,
        "failed_auth_recent": len(_failed_attempts),
    }


def render_system(flash=None):
    f = _system_facts()
    cpu = "?" if f["cpu_temp_c"] is None else ("%.1f \xb0C" % f["cpu_temp_c"])
    stats = [
        ("Board", f["board"]),
        ("Access Point IP", f["ap_ip"]),
        ("Uptime", f["uptime"]),
        ("Payloads", str(f["payload_count"])),
        ("Connected stations",
         "?" if f["stations_connected"] is None else str(f["stations_connected"])),
        ("Free memory", format_size(f["free_mem"])),
        ("Used memory", format_size(f["used_mem"])),
        ("CPU temperature", cpu),
        ("Filesystem", f["fs"]),
        ("Web auth", "enabled" if f["auth_enabled"] else "disabled"),
        ("API token", "set" if f["api_token_set"] else "not set"),
        ("Recent failed logins", "%d" % f["failed_auth_recent"]),
    ]
    cards = ''.join(
        '<div class="stat"><div class="k">%s</div>'
        '<div class="v">%s</div></div>' % (html_escape(k), html_escape(v))
        for k, v in stats
    )
    body = (
        '<div class="card"><h1>System</h1>'
        '<p class="muted">Live status of the device.</p>'
        '<div class="grid">%s</div>'
        '<div class="row" style="margin-top:16px">'
        '<a class="btn" href="/system">Refresh</a>'
        '<a class="btn" href="/api/system">JSON</a>'
        '<form method="post" action="/system/reboot" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Reboot the Pico now?\')">Reboot</button>'
        '</form></div></div>'
    ) % (cards, csrf_field())
    return layout("System", body, active="system", flash=flash)


# ---------------------------------------------------------------------------
# WSGI app
# ---------------------------------------------------------------------------
web_app = WSGIApp()


def _setup_gate(request, path):
    """If first-run setup is required, redirect everything to /setup."""
    if SETUP_REQUIRED and not path.startswith("/setup") and not path.startswith("/api/"):
        return redirect("/setup")
    return None


def _auth_gate(request):
    if is_locked_out():
        return locked_response()
    if not is_authenticated(request):
        record_failed_attempt()
        return unauthorized_response()
    return None


def _csrf_gate(form):
    if not csrf_ok(form):
        return csrf_failure_response()
    return None


# ---- Setup wizard ----
@web_app.route("/setup", methods=['GET', 'POST'])
def setup_route(request):
    if not SETUP_REQUIRED:
        return redirect("/")
    if request.method == 'GET':
        return html_response(render_setup(flash=get_flash(request)))
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/setup", flash="too_big")
    if not csrf_ok(form):
        return csrf_failure_response()
    user = (form.get('username') or '').strip()
    pw = form.get('password') or ''
    pw2 = form.get('password2') or ''
    token = (form.get('api_token') or '').strip()
    if len(user) < 3 or len(user) > 32:
        return html_response(render_setup(
            flash=("err", "Username must be 3-32 characters."),
            prefill_user=user))
    if len(pw) < 8:
        return html_response(render_setup(
            flash=("err", FLASH_MESSAGES["weak_pw"][1]),
            prefill_user=user))
    if pw != pw2:
        return html_response(render_setup(
            flash=("err", FLASH_MESSAGES["pw_mismatch"][1]),
            prefill_user=user))
    if token and len(token) < 16:
        return html_response(render_setup(
            flash=("err", "API token should be at least 16 characters."),
            prefill_user=user))
    try:
        apply_credentials(user, pw, token)
    except Exception as ex:
        print("setup write failed:", ex)
        return html_response(render_setup(
            flash=("err", "Could not write credentials. FS may be read-only."),
            prefill_user=user))
    audit("setup.complete", "user=%s api=%s" % (user, "yes" if token else "no"))
    return redirect("/", flash="setup_done")


# ---- Home / index ----
@web_app.route("/")
def index(request):
    gate = _setup_gate(request, "/")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_home(flash=get_flash(request)))


@web_app.route("/ducky")
def ducky_compat(request):
    return index(request)


# ---- New ----
@web_app.route("/new", methods=['GET', 'POST'])
def new_script(request):
    gate = _setup_gate(request, "/new")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    if request.method == 'GET':
        return html_response(render_new(flash=get_flash(request)))
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/new", flash="too_big")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    name = normalize_payload_name(form.get('scriptName', ''))
    if not name:
        return html_response(render_new(
            flash=("err", "Invalid filename. Use letters, digits, _, - only."),
            prefill_name=form.get('scriptName', ''),
            prefill_data=form.get('scriptData', '')))
    data = form.get('scriptData', '')
    if len(data) > MAX_PAYLOAD_BYTES:
        return redirect("/new", flash="too_big")
    if file_exists(name):
        return html_response(render_new(
            flash=("err", "A payload with that name already exists."),
            prefill_name=form.get('scriptName', ''),
            prefill_data=data))
    try:
        write_file_safely(name, data)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/new", flash="bad_request")
    audit("payload.create", name)
    return redirect("/edit/" + name, flash="saved")


# ---- Edit ----
@web_app.route("/edit/<filename>")
def edit(request, filename):
    gate = _setup_gate(request, "/edit")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    try:
        with open(name, "r") as f:
            contents = f.read()
    except OSError:
        return redirect("/", flash="not_found")
    return html_response(render_editor(name, contents,
                                       flash=get_flash(request)))


# ---- Write ----
@web_app.route("/write/<filename>", methods=['POST'])
def write_script(request, filename):
    gate = _setup_gate(request, "/write")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name:
        return redirect("/", flash="bad_name")
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="too_big")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    data = form.get('scriptData', '')
    if len(data) > MAX_PAYLOAD_BYTES:
        return redirect("/edit/" + name, flash="too_big")
    try:
        write_file_safely(name, data)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/edit/" + name, flash="bad_request")
    audit("payload.save", "%s %dB" % (name, len(data)))
    warnings = lint_payload(data)
    if warnings:
        # Show editor again with warnings AND a saved flash
        return html_response(render_editor(name, data,
                                           flash=("ok", "Saved with warnings."),
                                           warnings=warnings))
    return redirect("/edit/" + name, flash="saved")


# ---- Delete ----
@web_app.route("/delete/<filename>", methods=['POST'])
def delete_route(request, filename):
    gate = _setup_gate(request, "/delete")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    try:
        delete_file_safely(name)
    except Exception as ex:
        print("delete failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.delete", name)
    return redirect("/", flash="deleted")


# ---- Duplicate ----
@web_app.route("/duplicate/<filename>", methods=['POST'])
def duplicate_route(request, filename):
    gate = _setup_gate(request, "/duplicate")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    base = name[:-len(PAYLOAD_EXT)]
    new_name = None
    for i in range(2, 100):
        candidate = "%s_copy%d%s" % (base, i, PAYLOAD_EXT) if i > 2 else "%s_copy%s" % (base, PAYLOAD_EXT)
        if not file_exists(candidate) and len(candidate) <= MAX_FILENAME_LEN:
            new_name = candidate
            break
    if not new_name:
        return redirect("/", flash="bad_request")
    try:
        with open(name, "r") as f:
            data = f.read()
        write_file_safely(new_name, data)
    except Exception as ex:
        print("duplicate failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.duplicate", "%s -> %s" % (name, new_name))
    return redirect("/edit/" + new_name, flash="duplicated")


# ---- Rename ----
@web_app.route("/rename/<filename>", methods=['GET', 'POST'])
def rename_route(request, filename):
    gate = _setup_gate(request, "/rename")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    if request.method == 'GET':
        return html_response(render_rename(name, flash=get_flash(request)))
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    new_name = normalize_payload_name(form.get('newName', ''))
    if not new_name:
        return redirect("/rename/" + name, flash="bad_name")
    if new_name == name:
        return redirect("/", flash="renamed")
    if file_exists(new_name):
        return redirect("/rename/" + name, flash="exists")
    try:
        rename_file_safely(name, new_name)
    except Exception as ex:
        print("rename failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.rename", "%s -> %s" % (name, new_name))
    return redirect("/", flash="renamed")


# ---- Download ----
@web_app.route("/download/<filename>")
def download(request, filename):
    gate = _setup_gate(request, "/download")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    try:
        with open(name, "r") as f:
            contents = f.read()
    except OSError:
        return redirect("/", flash="not_found")
    audit("payload.download", name)
    return text_response(contents, filename=name)


# ---- Preview ----
@web_app.route("/preview/<filename>")
def preview_route(request, filename):
    gate = _setup_gate(request, "/preview")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    try:
        with open(name, "r") as f:
            contents = f.read()
    except OSError:
        return redirect("/", flash="not_found")
    return html_response(render_preview(name, contents))


# ---- Upload page ----
@web_app.route("/upload")
def upload_page(request):
    gate = _setup_gate(request, "/upload")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_upload(flash=get_flash(request)))


# ---- Snippets ----
@web_app.route("/snippets")
def snippets_page(request):
    gate = _setup_gate(request, "/snippets")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_snippets(flash=get_flash(request)))


# ---- Audit ----
@web_app.route("/audit")
def audit_page(request):
    gate = _setup_gate(request, "/audit")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_audit(flash=get_flash(request)))


@web_app.route("/audit/clear", methods=['POST'])
def audit_clear(request):
    gate = _setup_gate(request, "/audit/clear")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/audit", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    try:
        clear_audit_log()
    except Exception as ex:
        print("clear log failed:", ex)
        return redirect("/audit", flash="bad_request")
    audit("audit.clear")
    return redirect("/audit", flash="logs_cleared")


# ---- System ----
@web_app.route("/system")
def system_page(request):
    gate = _setup_gate(request, "/system")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_system(flash=get_flash(request)))


@web_app.route("/system/reboot", methods=['POST'])
def system_reboot(request):
    gate = _setup_gate(request, "/system/reboot")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/system", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    if microcontroller is None:
        return redirect("/system", flash="bad_request")
    audit("system.reboot")

    async def _later():
        await asyncio.sleep(0.5)
        microcontroller.reset()
    asyncio.create_task(_later())
    return html_response(layout("Rebooting",
        '<div class="card"><h1>Rebooting...</h1>'
        '<p class="muted">The device will be back online in a moment.</p>'
        '</div>', active="system"))


# ---- Panic wipe ----
@web_app.route("/wipe", methods=['POST'])
def wipe_route(request):
    gate = _setup_gate(request, "/wipe")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    removed = 0
    try:
        for name, _ in list_payloads():
            sf = safe_filename(name)
            if sf:
                try:
                    delete_file_safely(sf)
                    removed += 1
                except Exception:
                    pass
    except Exception as ex:
        print("wipe failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.wipe", "%d removed" % removed)
    return redirect("/", flash="wiped")


# ---- Logout (force browser to drop cached Basic auth credentials) ----
@web_app.route("/logout")
def logout_route(request):
    # Sending a 401 with a different realm string nudges most browsers
    # to forget the cached credentials for this site.
    body = layout("Signed out",
                  '<div class="card"><h1>Signed out</h1>'
                  '<p class="muted">You have been signed out. '
                  '<a href="/">Sign in again</a>.</p></div>')
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky - signed out"')],
            body)


# ---- Run ----
@web_app.route("/run/<filename>", methods=['GET', 'POST'])
async def run_script_route(request, filename):
    gate = _setup_gate(request, "/run")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    if request.method == 'POST':
        try:
            form = parse_form(request_body(request))
        except ValueError:
            return redirect("/", flash="bad_request")
        csrf = _csrf_gate(form)
        if csrf:
            return csrf
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    audit("payload.run", name)
    print("run_script", name)
    await runScript(name)
    return redirect("/", flash="ran")


def setPayload(payload_number):
    if payload_number == 1:
        return "payload.dd"
    return "payload" + str(payload_number) + ".dd"


# ---- API ----
@web_app.route("/api/run/<filenumber>")
async def api_run(request, filenumber):
    if not api_authenticated(request):
        return text_response("unauthorized", status="401 Unauthorized")
    try:
        n = int(filenumber)
    except (TypeError, ValueError):
        return text_response("bad request", status="400 Bad Request")
    if n < 1 or n > 99:
        return text_response("bad request", status="400 Bad Request")
    name = setPayload(n)
    if not file_exists(name):
        return text_response("not found", status="404 Not Found")
    audit("api.run", name)
    await runScript(name)
    return text_response("ok\n")


@web_app.route("/api/payloads")
def api_payloads(request):
    if not api_authenticated(request):
        return text_response("unauthorized", status="401 Unauthorized")
    body = '\n'.join("%s\t%d" % (n, s) for n, s in list_payloads()) + '\n'
    return text_response(body)


@web_app.route("/api/system")
def api_system(request):
    if not api_authenticated(request):
        return text_response("unauthorized", status="401 Unauthorized")
    return json_response(_system_facts())


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------
async def startWebService():
    HOST = repr(wifi.radio.ipv4_address_ap)
    PORT = 80
    print(HOST, PORT)
    wsgiServer = server.WSGIServer(PORT, application=web_app)
    print("open this IP in your browser: http://%s:%d/" % (HOST, PORT))
    if SETUP_REQUIRED:
        print("first-run setup required: browse to /setup to create credentials")
    elif AUTH_ENABLED:
        print("web UI auth enabled (creds.py)")
    else:
        print("web UI auth DISABLED")
    if API_TOKEN:
        print("API token gating enabled for /api/*")
    audit("web.start", "auth=%s api_token=%s" %
          (AUTH_ENABLED, "yes" if API_TOKEN else "no"))
    wsgiServer.start()
    while True:
        try:
            wsgiServer.update_poll()
        except Exception as ex:
            print("wsgi update error:", ex)
        await asyncio.sleep(0)
