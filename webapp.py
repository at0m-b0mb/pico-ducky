# License : GPLv2.0
# Original copyright (c) 2023  Dave Bailey (dbisu, @daveisu)
# Pico W Ducky web interface — heavily enhanced.
#
# This file is intentionally self-contained so that "copy webapp.py + boot.py +
# code.py + pins.py + duckyinpython.py + secrets.py" remains the install flow.

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

# ===========================================================================
# Credentials (optional — first-run setup wizard creates this file)
# ===========================================================================
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

# ===========================================================================
# Config / limits / paths
# ===========================================================================
MAX_PAYLOAD_BYTES = 64 * 1024
MAX_FILENAME_LEN = 48
PAYLOAD_EXT = ".dd"
BACKUP_EXT = ".dd.bak"
CREDS_PATH = "/creds.py"
AUDIT_LOG_PATH = "/audit.log"
AUDIT_LOG_MAX = 32 * 1024
PINS_PATH = "/pins.txt"
ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)

BOOT_TIME = time.monotonic()

# Login rate limiting (global; WSGI server doesn't reliably surface peer IP)
LOCKOUT_THRESHOLD = 5
LOCKOUT_SECONDS = 300
_failed_attempts = []

ACCENT_THEMES = ("cyan", "violet", "emerald", "amber", "rose", "sky")


def _make_csrf():
    try:
        return binascii.hexlify(os.urandom(16)).decode()
    except Exception:
        return "%x%x" % (int(time.monotonic() * 1000), id(object()))
CSRF_TOKEN = _make_csrf()


# ===========================================================================
# Tiny helpers
# ===========================================================================
def file_exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False


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


def js_escape(s):
    """Escape for embedding inside a single-quoted JS string."""
    if s is None:
        return ""
    return (s.replace("\\", "\\\\")
             .replace("'", "\\'")
             .replace("\n", "\\n")
             .replace("\r", "\\r")
             .replace("</", "<\\/"))


def py_escape(s):
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


def file_mtime(name):
    try:
        st = os.stat(name)
        return st[8] if len(st) > 8 else 0
    except Exception:
        return 0


# ===========================================================================
# Icon system (inline SVG)
# ===========================================================================
ICONS = {
    "edit":     '<path d="M11.5 1.5l3 3-8 8H3.5v-3l8-8z"/>',
    "preview":  '<path d="M1 8s2.5-5 7-5 7 5 7 5-2.5 5-7 5-7-5-7-5z"/><circle cx="8" cy="8" r="2.5"/>',
    "download": '<path d="M8 1.5v8m-3-3l3 3 3-3"/><path d="M2 12.5h12"/>',
    "upload":   '<path d="M8 14V6m-3 3l3-3 3 3"/><path d="M2 3.5h12"/>',
    "trash":    '<path d="M3 4h10M6 4V2.5h4V4M5 4l.7 9.2a1 1 0 0 0 1 .8h2.6a1 1 0 0 0 1-.8L11 4"/>',
    "play":     '<path d="M3 2.2v11.6L13 8z"/>',
    "clone":    '<path d="M5 5h7v8H5z"/><path d="M3 3h7v2"/>',
    "rename":   '<path d="M2 13l2-2 7-7 2 2-7 7-2 2H2v-2z"/>',
    "snippet":  '<path d="M5 3l-3 5 3 5"/><path d="M11 3l3 5-3 5"/>',
    "system":   '<circle cx="8" cy="8" r="2"/><path d="M8 1v2M8 13v2M1 8h2M13 8h2M3.2 3.2l1.4 1.4M11.4 11.4l1.4 1.4M3.2 12.8l1.4-1.4M11.4 4.6l1.4-1.4"/>',
    "audit":    '<path d="M3 2h7l3 3v9H3z"/><path d="M5 7h6M5 9h6M5 11h4"/>',
    "search":   '<circle cx="7" cy="7" r="4.5"/><path d="M10.5 10.5l3 3"/>',
    "plus":     '<path d="M8 3v10M3 8h10"/>',
    "reboot":   '<path d="M14 4v4h-4"/><path d="M14 8a6 6 0 1 1-1.8-4.3"/>',
    "logout":   '<path d="M9 3H3v10h6"/><path d="M11 5l3 3-3 3M14 8H6"/>',
    "lock":     '<path d="M4 7h8v6H4z"/><path d="M6 7V5a2 2 0 0 1 4 0v2"/>',
    "unlock":   '<path d="M4 7h8v6H4z"/><path d="M6 7V5a2 2 0 0 1 4 0"/>',
    "sun":      '<circle cx="8" cy="8" r="3"/><path d="M8 1v1.5M8 13.5V15M1 8h1.5M13.5 8H15M3.2 3.2l1 1M11.8 11.8l1 1M3.2 12.8l1-1M11.8 4.2l1-1"/>',
    "moon":     '<path d="M13.5 9.5A6 6 0 1 1 6.5 2.5a5 5 0 0 0 7 7z"/>',
    "menu":     '<path d="M2.5 4h11M2.5 8h11M2.5 12h11"/>',
    "info":     '<circle cx="8" cy="8" r="6.5"/><path d="M8 7v4M8 5v.1"/>',
    "keyboard": '<path d="M1.5 4.5h13v7h-13z"/><path d="M4 7h.1M6 7h.1M8 7h.1M10 7h.1M12 7h.1M5 9.5h6"/>',
    "warn":     '<path d="M8 1.5L15 14H1z"/><path d="M8 6v4M8 11.5v.1"/>',
    "ok":       '<circle cx="8" cy="8" r="6.5"/><path d="M5 8.2l2 2 4-4.4"/>',
    "back":     '<path d="M14 8H2M6 4L2 8l4 4"/>',
    "drag":     '<circle cx="6" cy="3.5" r="1"/><circle cx="10" cy="3.5" r="1"/><circle cx="6" cy="8" r="1"/><circle cx="10" cy="8" r="1"/><circle cx="6" cy="12.5" r="1"/><circle cx="10" cy="12.5" r="1"/>',
    "star":     '<path d="M8 1.7l1.96 4.1L14.5 6.5l-3.35 3 .92 4.5L8 11.8l-4.07 2.2.92-4.5-3.35-3 4.54-.7z"/>',
    "tag":      '<path d="M2 8.5l6.5-6.5h5v5L7 13.5z"/><circle cx="11" cy="5" r=".7" fill="currentColor"/>',
    "settings": '<circle cx="8" cy="8" r="2.5"/><path d="M13 8c0 .5-.05.9-.13 1.3l1.4 1-1.5 2.6-1.6-.5c-.6.5-1.3.9-2 1.1L9 15h-3l-.3-1.5c-.7-.2-1.4-.6-2-1.1l-1.6.5L.6 10.3l1.4-1A6 6 0 0 1 2 8c0-.4.05-.8.13-1.2L.7 5.7l1.5-2.6 1.6.5c.6-.5 1.3-.9 2-1.1L6 1h3l.3 1.5c.7.2 1.4.6 2 1.1l1.6-.5 1.5 2.6-1.4 1c.1.4.15.8.15 1.3z"/>',
    "copy":     '<rect x="5" y="5" width="9" height="9" rx="1"/><path d="M3 11V3a1 1 0 0 1 1-1h7"/>',
    "find":     '<circle cx="7" cy="7" r="4"/><path d="M10 10l3.5 3.5M2 13.5l1.5-1.5"/>',
    "sort":     '<path d="M3 5l3-3 3 3"/><path d="M13 11l-3 3-3-3"/>',
    "filter":   '<path d="M2 3h12L9.5 9v5L6.5 12V9z"/>',
    "close":    '<path d="M4 4l8 8M12 4l-8 8"/>',
    "save":     '<path d="M3 2h8l2 2v10H3z"/><path d="M5 2v4h6V2M5 14v-5h6v5"/>',
    "more":     '<circle cx="3.5" cy="8" r="1.2"/><circle cx="8" cy="8" r="1.2"/><circle cx="12.5" cy="8" r="1.2"/>',
    "diff":     '<path d="M5 1v14M11 1v14M2 5h6M8 11h6"/>',
    "template": '<rect x="2" y="3" width="12" height="10" rx="1"/><path d="M2 6h12M5 9h6M5 11h4"/>',
    "wand":     '<path d="M2 14l9-9M12 4l1.5-1.5M11 3l-.7-.7M13 5l.7.7M9 1.5v1M14 6.5v1"/>',
    "refresh":  '<path d="M14 8a6 6 0 1 1-1.8-4.3"/><path d="M14 2v4h-4"/>',
    "send":     '<path d="M14 2L2 7l5 2 2 5z"/>',
    "wifi":     '<path d="M2 6a8 8 0 0 1 12 0"/><path d="M4 9a5 5 0 0 1 8 0"/><circle cx="8" cy="12.5" r="1"/>',
    "shield":   '<path d="M8 1.5L2.5 4v5c0 3 2.5 5 5.5 6 3-1 5.5-3 5.5-6V4z"/>',
}


def icon(name, cls="ico"):
    body = ICONS.get(name, "")
    return ('<svg class="%s" viewBox="0 0 16 16" fill="none" stroke="currentColor" '
            'stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" '
            'aria-hidden="true">%s</svg>' % (cls, body))


# ===========================================================================
# CSS
# ===========================================================================
BASE_CSS = """
:root{
 --bg:#070b18;--panel:rgba(17,26,46,.78);--panel-2:#172241;--border:#22304d;
 --muted:#7c8bab;--text:#e6edf7;--accent:#22d3ee;--accent-2:#7c3aed;
 --danger:#f43f5e;--ok:#10b981;--warn:#f59e0b;--input-bg:#0a1226;
 --radius:12px;--shadow:0 10px 28px rgba(0,0,0,.35);--accent-rgb:34,211,238;
}
html[data-theme="light"]{
 --bg:#f6f8fc;--panel:rgba(255,255,255,.78);--panel-2:#f1f5f9;--border:#dbe3ee;
 --muted:#64748b;--text:#0f172a;--accent:#0891b2;--accent-2:#7c3aed;
 --danger:#dc2626;--ok:#059669;--warn:#d97706;--input-bg:#ffffff;
 --shadow:0 10px 28px rgba(15,23,42,.08);--accent-rgb:8,145,178;
}
html[data-theme="oled"]{
 --bg:#000000;--panel:rgba(10,16,28,.85);--panel-2:#0d1424;--border:#1a2440;
 --muted:#7c8bab;--text:#e6edf7;--input-bg:#05080f;
 --shadow:0 10px 32px rgba(0,0,0,.7);
}
html[data-accent="cyan"]{--accent:#22d3ee;--accent-2:#7c3aed;--accent-rgb:34,211,238}
html[data-accent="violet"]{--accent:#a78bfa;--accent-2:#22d3ee;--accent-rgb:167,139,250}
html[data-accent="emerald"]{--accent:#34d399;--accent-2:#22d3ee;--accent-rgb:52,211,153}
html[data-accent="amber"]{--accent:#fbbf24;--accent-2:#fb7185;--accent-rgb:251,191,36}
html[data-accent="rose"]{--accent:#fb7185;--accent-2:#a78bfa;--accent-rgb:251,113,133}
html[data-accent="sky"]{--accent:#38bdf8;--accent-2:#a78bfa;--accent-rgb:56,189,248}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,sans-serif;
 font-size:15px;line-height:1.5;min-height:100vh;
 transition:background-color .3s,color .3s}
body::before{
 content:"";position:fixed;inset:-20vmax;z-index:-1;pointer-events:none;
 background:
  radial-gradient(circle at 20% 15%, rgba(var(--accent-rgb),.20), transparent 45%),
  radial-gradient(circle at 80% 75%, rgba(124,58,237,.22), transparent 45%),
  radial-gradient(circle at 50% 90%, rgba(244,63,94,.10), transparent 50%);
 filter:blur(20px);animation:aurora 24s ease-in-out infinite alternate;
}
html[data-theme="light"] body::before{opacity:.55}
html[data-theme="oled"] body::before{opacity:.35}
@keyframes aurora{
 0%{transform:translate(0,0) rotate(0deg) scale(1)}
 50%{transform:translate(2vw,-3vh) rotate(2deg) scale(1.05)}
 100%{transform:translate(-2vw,2vh) rotate(-2deg) scale(1.02)}
}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:1040px;margin:0 auto;padding:24px 16px 80px}
header.app{display:flex;align-items:center;justify-content:space-between;gap:10px;
 padding:14px 22px;background:linear-gradient(135deg,var(--panel),var(--panel-2));
 border-bottom:1px solid var(--border);position:sticky;top:0;z-index:20;
 -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px)}
header.app .brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:16px}
header.app .brand .logo{width:32px;height:32px;border-radius:9px;
 background:linear-gradient(135deg,var(--accent),var(--accent-2));
 display:inline-flex;align-items:center;justify-content:center;
 color:#06121f;font-weight:800;font-size:16px;
 box-shadow:0 4px 14px rgba(var(--accent-rgb),.35)}
.brand .sub{color:var(--muted);font-weight:500;font-size:12px;margin-left:2px}
nav.app{display:flex;gap:4px;flex-wrap:wrap;align-items:center}
nav.app a{padding:7px 12px;border-radius:999px;font-size:14px;color:var(--muted);
 display:inline-flex;align-items:center;gap:6px;transition:.15s}
nav.app a.active,nav.app a:hover{background:rgba(var(--accent-rgb),.12);
 color:var(--text);text-decoration:none}
nav.app a.active{color:var(--accent)}
.icon-btn{background:transparent;border:1px solid var(--border);color:var(--text);
 width:36px;height:36px;border-radius:999px;cursor:pointer;display:inline-flex;
 align-items:center;justify-content:center;font-size:14px;
 transition:background .15s,border-color .15s,transform .1s;position:relative}
.icon-btn:hover{background:var(--panel-2);border-color:var(--accent);color:var(--accent)}
.icon-btn:active{transform:scale(.95)}
.icon-btn.active{color:var(--accent);border-color:var(--accent)}
.nav-toggle{display:none}
.status{display:inline-flex;align-items:center;gap:6px;font-size:12px;
 color:var(--muted);margin-right:6px}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--ok);
 box-shadow:0 0 8px var(--ok);display:inline-block}
.status-dot.warn{background:var(--warn);box-shadow:0 0 8px var(--warn)}
.status-dot.err{background:var(--danger);box-shadow:0 0 8px var(--danger);
 animation:pulse 1.5s ease-in-out infinite}
@keyframes pulse{50%{opacity:.45}}
h1,h2,h3{margin:0 0 .5em 0;letter-spacing:-.01em}
h1{font-size:24px;font-weight:700}
h2{font-size:18px;color:var(--text);font-weight:600}
h3{font-size:13px;color:var(--muted);font-weight:600;text-transform:uppercase;
 letter-spacing:.06em}
.card{background:var(--panel);border:1px solid var(--border);
 border-radius:var(--radius);padding:20px;margin-bottom:18px;
 box-shadow:var(--shadow);-webkit-backdrop-filter:blur(14px);
 backdrop-filter:blur(14px);animation:fadeUp .35s ease backwards}
.card+.card{animation-delay:.06s}
.card+.card+.card{animation-delay:.12s}
@keyframes fadeUp{from{opacity:0;transform:translateY(8px)}}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
.spacer{flex:1}
.muted{color:var(--muted)}
.ico{width:14px;height:14px;display:inline-block;vertical-align:-2px;flex-shrink:0}
.ico-lg{width:18px;height:18px}
.ico-xl{width:32px;height:32px}
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 14px;
 border:1px solid var(--border);background:var(--panel-2);color:var(--text);
 border-radius:9px;font-size:14px;cursor:pointer;text-decoration:none;
 font-family:inherit;position:relative;overflow:hidden;
 transition:background .15s,border-color .15s,transform .08s,color .15s}
.btn:hover{background:rgba(var(--accent-rgb),.08);border-color:var(--accent);
 color:var(--text);text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn.primary{background:linear-gradient(135deg,var(--accent),var(--accent-2));
 color:#06121f;border-color:transparent;font-weight:600;
 box-shadow:0 6px 18px rgba(var(--accent-rgb),.28)}
.btn.primary:hover{filter:brightness(1.08);color:#06121f}
.btn.primary::after{content:"";position:absolute;top:0;left:-100%;width:100%;height:100%;
 background:linear-gradient(90deg,transparent,rgba(255,255,255,.35),transparent);
 transition:left .55s ease}
.btn.primary:hover::after{left:100%}
.btn.danger{background:transparent;color:var(--danger);border-color:rgba(244,63,94,.4)}
.btn.danger:hover{background:rgba(244,63,94,.12);color:var(--danger)}
.btn.warn{background:transparent;color:var(--warn);border-color:rgba(245,158,11,.4)}
.btn.warn:hover{background:rgba(245,158,11,.12);color:var(--warn)}
.btn.ok{background:transparent;color:var(--ok);border-color:rgba(16,185,129,.4)}
.btn.ok:hover{background:rgba(16,185,129,.12);color:var(--ok)}
.btn.ghost{background:transparent}
.btn.small{padding:5px 10px;font-size:13px;border-radius:7px}
.btn[disabled]{opacity:.5;cursor:not-allowed}
.input,textarea,select{
 width:100%;padding:11px 13px;background:var(--input-bg);color:var(--text);
 border:1px solid var(--border);border-radius:9px;font-size:14px;
 font-family:inherit;outline:none;transition:border-color .15s,box-shadow .15s}
.input:focus,textarea:focus,select:focus{border-color:var(--accent);
 box-shadow:0 0 0 3px rgba(var(--accent-rgb),.15)}
textarea{resize:vertical;min-height:260px;line-height:1.55;tab-size:4;
 font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px}
label{display:block;margin-bottom:6px;font-size:13px;color:var(--muted)}
.field{margin-bottom:14px}
table{width:100%;border-collapse:collapse}
th,td{padding:11px 12px;text-align:left;border-bottom:1px solid var(--border)}
th{font-size:12px;text-transform:uppercase;letter-spacing:.05em;
 color:var(--muted);font-weight:600;user-select:none}
th.sortable{cursor:pointer}
th.sortable:hover{color:var(--accent)}
th.sortable::after{content:"";display:inline-block;width:0;height:0;margin-left:6px;
 vertical-align:middle;border-left:4px solid transparent;border-right:4px solid transparent;
 border-top:4px solid currentColor;opacity:0}
th.sortable.asc::after{border-bottom:4px solid currentColor;border-top:none;opacity:1}
th.sortable.desc::after{opacity:1}
tr:last-child td{border-bottom:none}
tr:hover td{background:rgba(var(--accent-rgb),.04)}
tr.pinned td{background:rgba(var(--accent-rgb),.05)}
.checkbox{accent-color:var(--accent);width:16px;height:16px;cursor:pointer}
.badge{display:inline-block;padding:3px 9px;border-radius:999px;font-size:12px;
 background:rgba(var(--accent-rgb),.1);color:var(--accent);
 border:1px solid rgba(var(--accent-rgb),.25);font-weight:600}
.badge.muted{background:var(--panel-2);color:var(--muted);border-color:var(--border)}
.chip{display:inline-flex;align-items:center;gap:3px;padding:2px 7px;
 border-radius:999px;font-size:11px;background:var(--panel-2);
 border:1px solid var(--border);color:var(--muted);
 margin:1px 2px;text-decoration:none}
.chip:hover{border-color:var(--accent);color:var(--accent);text-decoration:none}
.chip.active{background:rgba(var(--accent-rgb),.12);color:var(--accent);
 border-color:var(--accent)}
.alert{padding:12px 14px;border-radius:10px;margin-bottom:14px;
 border:1px solid var(--border)}
.alert.ok{background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.35);color:#10b981}
.alert.err{background:rgba(244,63,94,.1);border-color:rgba(244,63,94,.35);color:#f43f5e}
.alert.info{background:rgba(var(--accent-rgb),.08);border-color:rgba(var(--accent-rgb),.3);color:var(--accent)}
.alert.warn{background:rgba(245,158,11,.1);border-color:rgba(245,158,11,.35);color:#f59e0b}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}
.stat{background:var(--panel-2);border:1px solid var(--border);
 border-radius:10px;padding:14px}
.stat .k{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.05em}
.stat .v{font-size:20px;font-weight:600;margin-top:4px;word-break:break-all}
.hero{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:10px;
 margin-bottom:16px}
.hero .tile{background:var(--panel-2);border:1px solid var(--border);
 border-radius:10px;padding:14px;text-align:left;position:relative;overflow:hidden}
.hero .tile .k{color:var(--muted);font-size:12px;text-transform:uppercase;
 letter-spacing:.05em;display:flex;align-items:center;gap:6px}
.hero .tile .n{font-size:26px;font-weight:700;margin-top:4px;
 background:linear-gradient(135deg,var(--accent),var(--accent-2));
 -webkit-background-clip:text;background-clip:text;color:transparent}
.hero .tile .sub{color:var(--muted);font-size:12px;margin-top:2px}
.actions{display:flex;gap:6px;flex-wrap:wrap;justify-content:flex-end}
.actions form{margin:0}
.snippets{display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:8px}
.snip{padding:9px 12px;background:var(--panel-2);border:1px solid var(--border);
 border-radius:9px;cursor:pointer;font-family:ui-monospace,monospace;
 font-size:12px;text-align:left;color:var(--text);transition:.15s}
.snip:hover{border-color:var(--accent);color:var(--accent);transform:translateY(-1px)}
footer.app{padding:20px;text-align:center;color:var(--muted);font-size:12px}
.kbd{font-family:ui-monospace,monospace;background:var(--input-bg);padding:1px 6px;
 border:1px solid var(--border);border-radius:4px;font-size:12px;color:var(--text)}
.empty{padding:50px 20px;text-align:center;color:var(--muted)}
.empty svg{width:64px;height:64px;opacity:.5;margin-bottom:12px}
.code-preview{background:var(--input-bg);padding:14px;border-radius:10px;
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
 padding:12px;border:1px solid var(--border);border-radius:10px;
 max-height:60vh;overflow:auto;white-space:pre-wrap;word-break:break-all}
.warn-list{margin:8px 0 0 0;padding-left:18px}
.warn-list li{margin:4px 0;font-size:13px}
.shortcut{display:inline-flex;gap:4px;align-items:center;color:var(--muted);
 font-size:12px;margin-left:8px}
.dropzone{border:2px dashed var(--border);border-radius:14px;padding:36px;
 text-align:center;color:var(--muted);transition:.2s;cursor:pointer;
 background:rgba(255,255,255,.02)}
.dropzone:hover,.dropzone.over{border-color:var(--accent);
 background:rgba(var(--accent-rgb),.06);color:var(--text)}
.dropzone svg{width:44px;height:44px;margin-bottom:10px;opacity:.7}
.dropzone .name{margin-top:8px;color:var(--accent);font-family:ui-monospace,monospace;font-size:13px}
.bar{height:8px;background:var(--input-bg);border-radius:999px;overflow:hidden;
 border:1px solid var(--border)}
.bar > div{height:100%;background:linear-gradient(90deg,var(--accent),var(--accent-2));
 transition:width .3s}
.toast-host{position:fixed;top:84px;right:20px;z-index:50;
 display:flex;flex-direction:column;gap:10px;max-width:380px;pointer-events:none}
.toast{padding:12px 14px;border-radius:10px;background:var(--panel);
 border:1px solid var(--border);box-shadow:var(--shadow);
 -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px);
 animation:slideIn .25s ease;display:flex;align-items:center;gap:10px;
 pointer-events:auto;transition:opacity .3s,transform .3s}
.toast.fade{opacity:0;transform:translateX(20px)}
@keyframes slideIn{from{transform:translateX(20px);opacity:0}}
.toast.ok{border-left:3px solid var(--ok);color:var(--ok)}
.toast.err{border-left:3px solid var(--danger);color:var(--danger)}
.toast.warn{border-left:3px solid var(--warn);color:var(--warn)}
.toast.info{border-left:3px solid var(--accent);color:var(--accent)}
.toast .msg{color:var(--text);font-size:14px;flex:1}
.toast .close{background:transparent;border:none;color:var(--muted);cursor:pointer;
 padding:0;display:inline-flex}
.toast .close:hover{color:var(--text)}
.modal-back{position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:60;
 display:none;align-items:center;justify-content:center;padding:20px;
 -webkit-backdrop-filter:blur(4px);backdrop-filter:blur(4px)}
.modal-back.open{display:flex;animation:fadeIn .2s ease}
@keyframes fadeIn{from{opacity:0}}
.modal{background:var(--panel);border:1px solid var(--border);
 border-radius:var(--radius);padding:22px;max-width:520px;width:100%;
 box-shadow:var(--shadow);max-height:80vh;overflow:auto}
.modal h2{margin-bottom:14px}
.kbd-row{display:flex;justify-content:space-between;align-items:center;
 padding:8px 0;border-bottom:1px solid var(--border);font-size:14px}
.kbd-row:last-child{border-bottom:none}
.search-result{padding:10px 12px;border-bottom:1px solid var(--border)}
.search-result:last-child{border-bottom:none}
.search-result a{font-weight:600}
.search-result .line{font-family:ui-monospace,monospace;font-size:12px;
 color:var(--muted);margin-top:4px;white-space:pre-wrap;word-break:break-all}
.search-result mark{background:rgba(var(--accent-rgb),.25);color:var(--text);
 padding:0 2px;border-radius:3px}
/* Editor: line numbers + sticky save bar */
.editor-wrap{position:relative;display:flex;border:1px solid var(--border);
 border-radius:9px;overflow:hidden;background:var(--input-bg)}
.line-nums{margin:0;padding:11px 8px 11px 12px;background:rgba(0,0,0,.18);
 color:var(--muted);font-family:ui-monospace,monospace;font-size:13px;
 line-height:1.55;text-align:right;user-select:none;border-right:1px solid var(--border);
 overflow:hidden;white-space:pre;min-width:42px}
.editor-wrap textarea{border:none;border-radius:0;flex:1;outline:none;
 box-shadow:none;background:transparent}
.editor-wrap textarea:focus{box-shadow:none}
.sticky-bar{position:sticky;bottom:0;background:linear-gradient(180deg,
 transparent 0%,var(--panel) 30%,var(--panel) 100%);padding:14px 0 4px;
 margin-top:14px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.tags-row{margin:6px 0 0;display:flex;flex-wrap:wrap;align-items:center;gap:2px}
.bulk-bar{position:sticky;top:64px;background:var(--panel);border:1px solid var(--accent);
 border-radius:9px;padding:8px 12px;margin-bottom:10px;display:none;
 align-items:center;gap:8px;z-index:5;
 box-shadow:0 4px 14px rgba(var(--accent-rgb),.18)}
.bulk-bar.open{display:flex;animation:slideDown .2s ease}
@keyframes slideDown{from{opacity:0;transform:translateY(-8px)}}
.accent-swatch{width:22px;height:22px;border-radius:999px;border:2px solid var(--border);
 cursor:pointer;transition:.15s;padding:0;background:transparent}
.accent-swatch.active{border-color:var(--text);transform:scale(1.1)}
.accent-swatch[data-c="cyan"]{background:linear-gradient(135deg,#22d3ee,#7c3aed)}
.accent-swatch[data-c="violet"]{background:linear-gradient(135deg,#a78bfa,#22d3ee)}
.accent-swatch[data-c="emerald"]{background:linear-gradient(135deg,#34d399,#22d3ee)}
.accent-swatch[data-c="amber"]{background:linear-gradient(135deg,#fbbf24,#fb7185)}
.accent-swatch[data-c="rose"]{background:linear-gradient(135deg,#fb7185,#a78bfa)}
.accent-swatch[data-c="sky"]{background:linear-gradient(135deg,#38bdf8,#a78bfa)}
.diff-row{display:grid;grid-template-columns:60px 1fr;gap:8px;
 padding:2px 8px;font-family:ui-monospace,monospace;font-size:12px;
 white-space:pre-wrap;word-break:break-all}
.diff-row .gutter{color:var(--muted);text-align:right;user-select:none}
.diff-row.add{background:rgba(16,185,129,.08);color:#10b981}
.diff-row.del{background:rgba(244,63,94,.08);color:#f43f5e}
.diff-row.add .gutter,.diff-row.del .gutter{color:inherit}
.template-card{display:flex;gap:14px;padding:14px;border:1px solid var(--border);
 border-radius:10px;margin-bottom:10px;background:var(--panel-2);
 transition:.15s;text-decoration:none;color:var(--text)}
.template-card:hover{border-color:var(--accent);transform:translateY(-1px);
 text-decoration:none}
.template-card .ico-host{flex-shrink:0;width:42px;height:42px;border-radius:9px;
 background:rgba(var(--accent-rgb),.12);display:flex;align-items:center;
 justify-content:center;color:var(--accent)}
.template-card .body{flex:1}
.template-card h2{font-size:15px;margin:0 0 4px}
.template-card p{margin:0;font-size:13px;color:var(--muted)}
.dropdown{position:relative;display:inline-block}
.dropdown-menu{display:none;position:absolute;right:0;top:calc(100% + 4px);
 background:var(--panel);border:1px solid var(--border);border-radius:9px;
 padding:6px;min-width:180px;z-index:30;box-shadow:var(--shadow);
 -webkit-backdrop-filter:blur(14px);backdrop-filter:blur(14px)}
.dropdown.open .dropdown-menu{display:block;animation:fadeIn .15s ease}
.dropdown-menu button,.dropdown-menu a{display:flex;align-items:center;gap:8px;
 padding:7px 10px;border-radius:7px;background:transparent;border:none;
 width:100%;text-align:left;color:var(--text);font-size:13px;font-family:inherit;
 cursor:pointer;text-decoration:none}
.dropdown-menu button:hover,.dropdown-menu a:hover{background:var(--panel-2);
 color:var(--accent);text-decoration:none}
.dropdown-menu button.danger{color:var(--danger)}
@media(max-width:760px){
 header.app{flex-wrap:wrap;padding:12px 14px}
 .nav-toggle{display:inline-flex}
 nav.app{display:none;flex-direction:column;align-items:stretch;width:100%;
  padding:8px 0;margin-top:8px;border-top:1px solid var(--border)}
 nav.app.open{display:flex;animation:fadeIn .15s ease}
 nav.app a{justify-content:flex-start}
 .wrap{padding:16px 12px}
 th.hide-sm,td.hide-sm{display:none}
 .toast-host{top:auto;bottom:20px;right:12px;left:12px;max-width:none}
 .bulk-bar{top:140px}
}
"""

THEME_BOOT_JS = (
    "(function(){try{"
    "var t=localStorage.getItem('pdtheme')||'dark';"
    "var a=localStorage.getItem('pdaccent')||'cyan';"
    "document.documentElement.setAttribute('data-theme',t);"
    "document.documentElement.setAttribute('data-accent',a);"
    "}catch(e){}})();"
)

GLOBAL_JS = """
(function(){
  // Toasts: auto-dismiss + manual close
  setTimeout(function(){
    document.querySelectorAll('.toast').forEach(function(t){
      t.classList.add('fade');
      setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t)},350);
    });
  },4500);
  document.querySelectorAll('.toast .close').forEach(function(b){
    b.addEventListener('click',function(){
      var t=b.parentNode;t.classList.add('fade');
      setTimeout(function(){if(t.parentNode)t.parentNode.removeChild(t)},250);
    });
  });
  // Mobile nav toggle
  var nt=document.getElementById('navToggle'),na=document.getElementById('navMain');
  if(nt&&na)nt.addEventListener('click',function(){na.classList.toggle('open')});
  // Theme toggle (cycles dark → light → oled)
  var tb=document.getElementById('themeBtn');
  if(tb)tb.addEventListener('click',function(){
    var d=document.documentElement,cur=d.getAttribute('data-theme')||'dark';
    var next=cur==='dark'?'light':cur==='light'?'oled':'dark';
    d.setAttribute('data-theme',next);
    try{localStorage.setItem('pdtheme',next)}catch(e){}
  });
  // Keyboard shortcuts modal
  var km=document.getElementById('kbdModal');
  function openKM(){if(km)km.classList.add('open')}
  function closeKM(){
    document.querySelectorAll('.modal-back.open').forEach(function(m){
      m.classList.remove('open');
    });
  }
  document.addEventListener('keydown',function(e){
    var tag=(e.target&&e.target.tagName)||'';
    var inField=tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT';
    if(!inField&&e.key==='?'){e.preventDefault();openKM()}
    if(e.key==='Escape')closeKM();
  });
  var kbBtn=document.getElementById('kbdBtn');
  if(kbBtn)kbBtn.addEventListener('click',openKM);
  document.querySelectorAll('.modal-back').forEach(function(m){
    m.addEventListener('click',function(e){if(e.target===m)closeKM()});
  });
  document.querySelectorAll('[data-close-modal]').forEach(function(b){
    b.addEventListener('click',closeKM);
  });
  // Dropdowns
  document.querySelectorAll('.dropdown > .icon-btn, .dropdown > .btn').forEach(function(b){
    b.addEventListener('click',function(e){
      e.stopPropagation();
      var d=b.parentNode;
      document.querySelectorAll('.dropdown.open').forEach(function(o){
        if(o!==d)o.classList.remove('open');
      });
      d.classList.toggle('open');
    });
  });
  document.addEventListener('click',function(){
    document.querySelectorAll('.dropdown.open').forEach(function(o){
      o.classList.remove('open');
    });
  });
})();
"""


# ===========================================================================
# Pins
# ===========================================================================
def read_pins():
    if not file_exists(PINS_PATH):
        return set()
    try:
        with open(PINS_PATH, "r") as f:
            return set(ln.strip() for ln in f.read().splitlines() if ln.strip())
    except OSError:
        return set()


def write_pins(pins):
    try:
        storage.remount("/", readonly=False)
        with open(PINS_PATH, "w") as f:
            for p in sorted(pins):
                f.write(p + "\n")
    finally:
        try:
            storage.remount("/", readonly=True)
        except Exception:
            pass


def toggle_pin(name):
    pins = read_pins()
    if name in pins:
        pins.discard(name)
        action = "off"
    else:
        pins.add(name)
        action = "on"
    write_pins(pins)
    return action


# ===========================================================================
# Tags (parsed from `REM TAGS: a, b, c` in first 20 lines)
# ===========================================================================
def parse_tags(text, max_lines=20):
    tags = []
    if not text:
        return tags
    for i, line in enumerate(text.splitlines()):
        if i >= max_lines:
            break
        s = line.strip()
        u = s.upper()
        if u.startswith("REM TAGS:") or u.startswith("REM TAGS "):
            body = s.split(":", 1)[1] if ":" in s else s[8:]
            for t in body.split(","):
                t = t.strip().lower()
                if t and len(t) <= 20 and all(
                        c.isalpha() or c.isdigit() or c in "_-" for c in t):
                    if t not in tags:
                        tags.append(t)
            break
    return tags


def tags_for(name):
    """Read the first 2 KB of a file and parse tags from it."""
    try:
        with open(name, "r") as f:
            head = f.read(2048)
    except OSError:
        return []
    return parse_tags(head)


# ===========================================================================
# Templates library
# ===========================================================================
TEMPLATES = [
    {
        "key": "hello",
        "name": "Hello, world",
        "desc": "Open a text editor and type a greeting. Safe starter.",
        "tags": "starter,demo",
        "body": (
            "REM TAGS: starter, demo\n"
            "REM Open Notepad (Windows) and say hi.\n"
            "DEFAULT_DELAY 50\n"
            "GUI r\n"
            "DELAY 300\n"
            "STRING notepad\n"
            "ENTER\n"
            "DELAY 800\n"
            "STRINGLN Hello from the Pico Ducky!\n"
        ),
    },
    {
        "key": "win_recon",
        "name": "Windows: open PowerShell",
        "desc": "Launch an elevated-ish PowerShell window for recon work.",
        "tags": "windows,powershell",
        "body": (
            "REM TAGS: windows, powershell\n"
            "DEFAULT_DELAY 75\n"
            "GUI r\n"
            "DELAY 300\n"
            "STRING powershell\n"
            "ENTER\n"
        ),
    },
    {
        "key": "win_lock",
        "name": "Windows: lock the screen",
        "desc": "Hit Win+L. Useful as a panic/finish step.",
        "tags": "windows,safety",
        "body": (
            "REM TAGS: windows, safety\n"
            "GUI l\n"
        ),
    },
    {
        "key": "mac_spotlight",
        "name": "macOS: open Spotlight",
        "desc": "Cmd+Space then type. Cross-platform via COMMAND key.",
        "tags": "macos",
        "body": (
            "REM TAGS: macos\n"
            "DEFAULT_DELAY 60\n"
            "COMMAND SPACE\n"
            "DELAY 250\n"
            "STRING TextEdit\n"
            "ENTER\n"
            "DELAY 1000\n"
            "STRINGLN Hello from the Pico Ducky!\n"
        ),
    },
    {
        "key": "loop",
        "name": "Repeat with a counter",
        "desc": "WHILE loop that types 'tick' five times. Good template for retries.",
        "tags": "control-flow,demo",
        "body": (
            "REM TAGS: control-flow, demo\n"
            "VAR $I = 0\n"
            "WHILE ( $I < 5 )\n"
            "  STRING tick\n"
            "  ENTER\n"
            "  $I = $I + 1\n"
            "END_WHILE\n"
        ),
    },
    {
        "key": "if_caps",
        "name": "Branch on caps-lock",
        "desc": "Pick a different action depending on host caps-lock state.",
        "tags": "control-flow",
        "body": (
            "REM TAGS: control-flow\n"
            "IF ( $_CAPSLOCK_ON == TRUE ) THEN\n"
            "  STRINGLN caps was on\n"
            "ELSE\n"
            "  STRINGLN caps was off\n"
            "END_IF\n"
        ),
    },
    {
        "key": "random",
        "name": "Random ID",
        "desc": "Print a random integer in a range.",
        "tags": "demo",
        "body": (
            "REM TAGS: demo\n"
            "$_RANDOM_MIN = 1000\n"
            "$_RANDOM_MAX = 9999\n"
            "STRINGLN ID = $_RANDOM_INT\n"
        ),
    },
    {
        "key": "exfil",
        "name": "Exfil mode opt-in",
        "desc": "Enables loot capture. Read README before using.",
        "tags": "exfil,advanced",
        "body": (
            "REM TAGS: exfil, advanced\n"
            "REM $_EXFIL_MODE_ENABLED = TRUE\n"
            "REM $_EXFIL_LEDS_ENABLED = TRUE\n"
            "REM Place your post-exfil keystrokes below.\n"
        ),
    },
]


def template_by_key(key):
    for t in TEMPLATES:
        if t["key"] == key:
            return t
    return None


# ===========================================================================
# Layout chrome
# ===========================================================================
NAV_ITEMS = (
    ("home",      "/",          "Payloads",  "snippet"),
    ("new",       "/new",       "New",       "plus"),
    ("templates", "/templates", "Templates", "template"),
    ("upload",    "/upload",    "Upload",    "upload"),
    ("search",    "/search",    "Search",    "search"),
    ("snippets",  "/snippets",  "Snippets",  "wand"),
    ("audit",     "/audit",     "Audit",     "audit"),
    ("system",    "/system",    "System",    "system"),
    ("settings",  "/settings",  "Settings",  "settings"),
)


def nav_html(active):
    out = ""
    for k, href, label, ico in NAV_ITEMS:
        cls = ' class="active"' if k == active else ""
        out += '<a href="%s"%s>%s%s</a>' % (
            href, cls, icon(ico), html_escape(label))
    return out


def status_indicator():
    if is_locked_out():
        return ('<span class="status" title="Locked out">'
                '<span class="status-dot err"></span>locked</span>')
    if not AUTH_ENABLED:
        return ('<span class="status" title="Auth disabled">'
                '<span class="status-dot warn"></span>open</span>')
    return ('<span class="status" title="Secured">'
            '<span class="status-dot"></span>secured</span>')


def kbd_modal_html():
    rows = [
        ("?", "Show this help"),
        ("Esc", "Close any open modal"),
        ("Ctrl/Cmd + S", "Save (in editor)"),
        ("Ctrl/Cmd + F", "Find / replace (in editor)"),
        ("/", "Focus search (on Search page)"),
        ("Click ⭐", "Pin or unpin a payload"),
        ("Click a snippet", "Insert at cursor (in editor)"),
    ]
    rs = ''.join(
        '<div class="kbd-row"><span>%s</span><span class="kbd">%s</span></div>'
        % (html_escape(label), html_escape(combo))
        for combo, label in rows
    )
    return (
        '<div class="modal-back" id="kbdModal">'
        '<div class="modal" role="dialog" aria-modal="true">'
        '<div class="row"><h2>%s Keyboard shortcuts</h2>'
        '<div class="spacer"></div>'
        '<button class="icon-btn" data-close-modal title="Close">%s</button>'
        '</div>%s</div></div>'
    ) % (icon("keyboard", "ico ico-lg"), icon("close"), rs)


def toast_html(flash):
    if not flash:
        return ""
    kind, msg = flash
    ico = "ok" if kind == "ok" else ("warn" if kind == "warn"
                                     else "info" if kind == "info" else "warn")
    return (
        '<div class="toast-host"><div class="toast %s">'
        '%s<span class="msg">%s</span>'
        '<button class="close" type="button" aria-label="Dismiss">%s</button>'
        '</div></div>'
    ) % (kind, icon(ico), html_escape(msg), icon("close"))


def layout(title, body, active="home", flash=None):
    header = (
        '<header class="app"><div class="brand">'
        '<span class="logo">D</span>Pico Ducky'
        '<span class="sub hide-sm">&middot; secure control panel</span>'
        '</div>%s'
        '<nav class="app" id="navMain">%s</nav>'
        '<div class="row" style="gap:6px">'
        '<button type="button" class="icon-btn" id="kbdBtn" title="Keyboard shortcuts (?)">%s</button>'
        '<button type="button" class="icon-btn" id="themeBtn" title="Theme (dark / light / oled)">%s</button>'
        '%s'
        '<button type="button" class="icon-btn nav-toggle" id="navToggle" title="Menu">%s</button>'
        '</div></header>'
    ) % (
        status_indicator(), nav_html(active),
        icon("keyboard"), icon("sun"),
        ('<a class="icon-btn" href="/logout" title="Sign out">%s</a>' % icon("logout"))
            if AUTH_ENABLED else '',
        icon("menu"),
    )
    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8"><title>%s &middot; Pico Ducky</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<meta name="color-scheme" content="dark light">'
        '<style>%s</style><script>%s</script></head><body>'
        '%s%s'
        '<div class="wrap">%s</div>'
        '<footer class="app">pico-ducky &middot; secured by you &middot; '
        '<a href="/system">system</a> &middot; '
        'press <span class="kbd">?</span> for shortcuts</footer>'
        '%s<script>%s</script></body></html>'
    ) % (html_escape(title), BASE_CSS, THEME_BOOT_JS,
         header, toast_html(flash), body, kbd_modal_html(), GLOBAL_JS)


# ===========================================================================
# Request / form helpers
# ===========================================================================
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
        return {}, []
    if isinstance(raw, (bytes, bytearray)):
        try:
            raw = raw.decode('utf-8')
        except UnicodeError:
            raw = raw.decode('latin-1')
    form = {}
    multi = []  # preserve duplicates for things like checkbox lists
    for pair in raw.split('&'):
        if not pair:
            continue
        if '=' in pair:
            k, v = pair.split('=', 1)
        else:
            k, v = pair, ''
        k, v = cleanup_text(k), cleanup_text(v)
        multi.append((k, v))
        form[k] = v
    return form, multi


def parse_form_single(raw):
    return parse_form(raw)[0]


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


# ===========================================================================
# Auth + rate limiting
# ===========================================================================
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
    body = ('<div class="card"><h1>%s Authentication required</h1>'
            '<p class="muted">%s</p></div>') % (
        icon("lock", "ico ico-lg"), html_escape(msg))
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky"')],
            layout("Sign in", body))


def locked_response():
    body = ('<div class="card"><h1>%s Too many failed attempts</h1>'
            '<p class="muted">Try again in ~%d seconds. '
            'Power-cycle the Pico to reset immediately.</p></div>') % (
        icon("warn", "ico ico-lg"), lockout_remaining())
    return ("429 Too Many Requests",
            [("Content-Type", "text/html; charset=utf-8"),
             ("Retry-After", str(LOCKOUT_SECONDS))],
            layout("Locked", body))


# ===========================================================================
# CSRF
# ===========================================================================
def csrf_field():
    return '<input type="hidden" name="_csrf" value="%s">' % CSRF_TOKEN


def csrf_ok(form):
    token = form.get('_csrf', '') if hasattr(form, 'get') else ''
    if not token:
        return False
    return _ct_equals(token, CSRF_TOKEN)


def csrf_failure_response():
    body = ('<div class="card"><h1>%s Security check failed</h1>'
            '<p class="muted">CSRF token missing or invalid. '
            'Reload the page and try again.</p>'
            '<p><a class="btn" href="/">%s Back</a></p></div>') % (
        icon("warn", "ico ico-lg"), icon("back"))
    return ("403 Forbidden",
            [("Content-Type", "text/html; charset=utf-8")],
            layout("Forbidden", body))


# ===========================================================================
# Response helpers
# ===========================================================================
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


# ===========================================================================
# Filename validation + listing
# ===========================================================================
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


def list_payloads_detailed():
    """Returns a list of (name, size, mtime, pinned, tags)."""
    pins = read_pins()
    out = []
    try:
        for f in os.listdir("/"):
            if f.endswith(PAYLOAD_EXT) and not f.endswith(BACKUP_EXT):
                out.append((f, file_size(f), file_mtime(f),
                            f in pins, tags_for(f)))
    except OSError:
        pass
    # pinned first, then alpha
    out.sort(key=lambda r: (0 if r[3] else 1, r[0].lower()))
    return out


def list_payloads():
    """Simple (name, size, mtime) — used by external bits."""
    return [(n, s, m) for (n, s, m, _, _) in list_payloads_detailed()]


# ===========================================================================
# FS write helpers
# ===========================================================================
def _remount_rw():
    storage.remount("/", readonly=False)


def _remount_ro():
    try:
        storage.remount("/", readonly=True)
    except Exception:
        pass


def write_file_safely(name, contents, backup=True):
    if (len(contents) > MAX_PAYLOAD_BYTES
            and not name.startswith("/audit") and name != CREDS_PATH):
        raise ValueError("payload exceeds %d bytes" % MAX_PAYLOAD_BYTES)
    try:
        _remount_rw()
        if backup and name.endswith(PAYLOAD_EXT) and file_exists(name):
            try:
                with open(name, "r") as fr:
                    prev = fr.read()
                with open(name + ".bak", "w") as fb:
                    fb.write(prev)
            except Exception as ex:
                print("backup failed:", ex)
        with open(name, "w") as f:
            f.write(contents)
    finally:
        _remount_ro()


def delete_file_safely(name):
    try:
        _remount_rw()
        os.remove(name)
        bak = name + ".bak"
        if file_exists(bak):
            try:
                os.remove(bak)
            except Exception:
                pass
    finally:
        _remount_ro()


def rename_file_safely(old, new):
    try:
        _remount_rw()
        os.rename(old, new)
    finally:
        _remount_ro()


# ===========================================================================
# Audit log
# ===========================================================================
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


# ===========================================================================
# Credentials persistence + factory reset
# ===========================================================================
def _write_creds(user, pw, token):
    lines = [
        '# Generated by the Pico Ducky setup wizard / settings page.',
        'WEB_USERNAME = "%s"' % py_escape(user),
        'WEB_PASSWORD = "%s"' % py_escape(pw),
    ]
    if token:
        lines.append('API_TOKEN = "%s"' % py_escape(token))
    write_file_safely(CREDS_PATH, "\n".join(lines) + "\n", backup=False)


def apply_credentials(username, password, api_token=""):
    global AUTH_ENABLED, WEB_USERNAME, WEB_PASSWORD, API_TOKEN, SETUP_REQUIRED
    WEB_USERNAME = username
    WEB_PASSWORD = password
    API_TOKEN = api_token or ""
    AUTH_ENABLED = bool(username and password)
    _write_creds(username, password, API_TOKEN)
    SETUP_REQUIRED = False


def rotate_api_token():
    global API_TOKEN
    new_token = _make_csrf() + _make_csrf()  # 64 hex chars
    API_TOKEN = new_token
    _write_creds(WEB_USERNAME, WEB_PASSWORD, new_token)
    return new_token


def clear_api_token():
    global API_TOKEN
    API_TOKEN = ""
    _write_creds(WEB_USERNAME, WEB_PASSWORD, "")


def factory_reset():
    """Wipe payloads, backups, audit log, pins, and creds."""
    global SETUP_REQUIRED, AUTH_ENABLED, WEB_USERNAME, WEB_PASSWORD, API_TOKEN
    try:
        _remount_rw()
        for f in list(os.listdir("/")):
            if f.endswith(PAYLOAD_EXT) or f.endswith(BACKUP_EXT):
                try:
                    os.remove(f)
                except Exception:
                    pass
        for p in (AUDIT_LOG_PATH, PINS_PATH, CREDS_PATH):
            if file_exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
    finally:
        _remount_ro()
    WEB_USERNAME = ""
    WEB_PASSWORD = ""
    API_TOKEN = ""
    AUTH_ENABLED = False
    SETUP_REQUIRED = True


# ===========================================================================
# DuckyScript lint + highlighter
# ===========================================================================
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
    "F1","F2","F3","F4","F5","F6","F7","F8","F9","F10",
    "F11","F12","F13","F14","F15","F16","F17","F18","F19",
    "F20","F21","F22","F23","F24",
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
        if line.startswith("$") or line.startswith("REM"):
            consec_delays = 0
            continue
        parts = line.split(None, 1)
        cmd = parts[0].upper()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd == "DELAY":
            consec_delays += 1
            if consec_delays >= 3:
                warnings.append(
                    "Line %d: many consecutive DELAYs; consider DEFAULT_DELAY." % idx)
        else:
            consec_delays = 0
        if cmd not in KNOWN_COMMANDS:
            warnings.append("Line %d: unknown command '%s'. Typo?" % (idx, cmd))
        if cmd in CMD_NEEDS_ARG and not arg:
            warnings.append("Line %d: '%s' is missing an argument." % (idx, cmd))
        if cmd in ("DELAY", "DEFAULT_DELAY", "DEFAULTDELAY", "STRING_DELAY") and arg:
            try:
                v = int(arg.split()[0])
                if v < 0:
                    warnings.append(
                        "Line %d: '%s' value should be non-negative." % (idx, cmd))
            except ValueError:
                if not arg.lstrip().startswith("$"):
                    warnings.append(
                        "Line %d: '%s' expects an integer." % (idx, cmd))
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
        out.append('<span class="tok-cmd">%s</span>' % html_escape(cmd))
        if rest:
            out.append(" ")
            cmd_u = cmd.upper()
            if cmd_u in ("STRING", "STRINGLN", "PRINTSTRING"):
                out.append('<span class="tok-str">%s</span>' % html_escape(rest))
            else:
                buf = ""
                i = 0
                while i < len(rest):
                    c = rest[i]
                    if c == '$':
                        if buf:
                            out.append(html_escape(buf)); buf = ""
                        j = i + 1
                        while j < len(rest) and (rest[j].isalpha()
                                                 or rest[j].isdigit()
                                                 or rest[j] == '_'):
                            j += 1
                        out.append('<span class="tok-var">%s</span>'
                                   % html_escape(rest[i:j]))
                        i = j
                    elif c.isdigit():
                        if buf:
                            out.append(html_escape(buf)); buf = ""
                        j = i
                        while j < len(rest) and rest[j].isdigit():
                            j += 1
                        out.append('<span class="tok-num">%s</span>'
                                   % html_escape(rest[i:j]))
                        i = j
                    elif c in "=+-*/<>!&|()":
                        if buf:
                            out.append(html_escape(buf)); buf = ""
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


# ===========================================================================
# Diff (line-by-line, simple LCS-free; readability over precision)
# ===========================================================================
def render_diff_lines(old, new):
    """Return HTML rows of a simple line diff between old and new."""
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    old_set = {l: i for i, l in enumerate(old_lines)}
    rows = []
    nidx = 0
    oidx = 0
    while nidx < len(new_lines) or oidx < len(old_lines):
        on = old_lines[oidx] if oidx < len(old_lines) else None
        nn = new_lines[nidx] if nidx < len(new_lines) else None
        if on is None:
            rows.append(('add', nidx + 1, nn)); nidx += 1
        elif nn is None:
            rows.append(('del', oidx + 1, on)); oidx += 1
        elif on == nn:
            rows.append(('ctx', nidx + 1, on)); nidx += 1; oidx += 1
        else:
            # If new line exists later in old, treat current old as deletion
            if nn in old_set and old_set[nn] >= oidx:
                rows.append(('del', oidx + 1, on)); oidx += 1
            elif on in new_lines[nidx:]:
                rows.append(('add', nidx + 1, nn)); nidx += 1
            else:
                rows.append(('del', oidx + 1, on))
                rows.append(('add', nidx + 1, nn))
                oidx += 1; nidx += 1
    return rows


# ===========================================================================
# Flash messages
# ===========================================================================
FLASH_MESSAGES = {
    "saved":         ("ok",   "Payload saved."),
    "deleted":       ("ok",   "Payload deleted."),
    "renamed":       ("ok",   "Payload renamed."),
    "uploaded":      ("ok",   "Payload uploaded."),
    "ran":           ("ok",   "Payload executed."),
    "duplicated":    ("ok",   "Payload duplicated."),
    "wiped":         ("ok",   "All payloads wiped."),
    "logs_cleared":  ("ok",   "Audit log cleared."),
    "setup_done":    ("ok",   "Setup complete. Sign in to continue."),
    "restored":      ("ok",   "Previous version restored."),
    "pinned":        ("ok",   "Pinned."),
    "unpinned":      ("ok",   "Unpinned."),
    "pw_changed":    ("ok",   "Password changed. Sign in again."),
    "token_rotated": ("ok",   "New API token generated."),
    "token_cleared": ("ok",   "API token cleared."),
    "factory_reset": ("ok",   "Factory reset complete. Set up the device again."),
    "bulk_done":     ("ok",   "Bulk action completed."),
    "bad_name":      ("err",  "Invalid filename. Use A-Z, 0-9, _, - and end in .dd"),
    "too_big":       ("err",  "Payload too large."),
    "not_found":     ("err",  "Payload not found."),
    "exists":        ("err",  "A payload with that name already exists."),
    "bad_request":   ("err",  "Bad request."),
    "no_backup":     ("err",  "No backup available for this payload."),
    "weak_pw":       ("err",  "Password must be at least 8 characters."),
    "pw_mismatch":   ("err",  "Passwords do not match."),
    "wrong_pw":      ("err",  "Current password is incorrect."),
}


def get_flash(request):
    key = query_get(request, 'm')
    if key:
        return FLASH_MESSAGES.get(key)
    return None


# ===========================================================================
# Snippet library
# ===========================================================================
SNIPPETS = [
    ("Comment",          "REM This is a comment"),
    ("Tags header",      "REM TAGS: starter, demo"),
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


# ===========================================================================
# System facts
# ===========================================================================
def _storage_usage():
    try:
        st = os.statvfs("/")
        bsize = st[1] or st[0]
        total = bsize * st[2]
        free = bsize * st[3]
        used = total - free
        return total, used, free
    except Exception:
        return None, None, None


def _wifi_facts():
    info = {"ap_mac": "?", "channel": "?", "ap_ssid": "?"}
    try:
        m = wifi.radio.mac_address_ap
        info["ap_mac"] = ":".join("%02X" % b for b in m)
    except Exception:
        pass
    for attr in ("ap_channel", "channel"):
        try:
            v = getattr(wifi.radio, attr, None)
            if v is not None:
                info["channel"] = str(v)
                break
        except Exception:
            pass
    try:
        ai = wifi.radio.ap_info
        if ai and getattr(ai, "ssid", None):
            info["ap_ssid"] = ai.ssid
    except Exception:
        pass
    return info


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
    total_b, used_b, free_b = _storage_usage()
    wifi_info = _wifi_facts()
    return {
        "board": getattr(board, 'board_id', '?'),
        "ap_ip": ap_ip,
        "ap_mac": wifi_info["ap_mac"],
        "ap_channel": wifi_info["channel"],
        "ap_ssid": wifi_info["ap_ssid"],
        "uptime_s": int(time.monotonic() - BOOT_TIME),
        "uptime": format_uptime(time.monotonic() - BOOT_TIME),
        "free_mem": free,
        "used_mem": alloc,
        "cpu_temp_c": cpu_temp,
        "fs": fs,
        "fs_total": total_b,
        "fs_used": used_b,
        "fs_free": free_b,
        "auth_enabled": AUTH_ENABLED,
        "api_token_set": bool(API_TOKEN),
        "payload_count": len(list_payloads()),
        "stations_connected": stations,
        "failed_auth_recent": len(_failed_attempts),
    }


# ===========================================================================
# Page renderers
# ===========================================================================
def render_setup(flash=None, prefill_user=""):
    body = (
        '<div class="card"><h1>%s First-time setup</h1>'
        '<p class="muted">Set credentials for the web UI. These are written to '
        '<span class="kbd">creds.py</span> on the device — keep that file safe.</p>'
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
        '<button class="btn primary" type="submit">%s Save and lock down</button>'
        '</div></form></div>'
    ) % (icon("lock", "ico ico-lg"), CSRF_TOKEN,
         html_escape(prefill_user), icon("lock"))
    return layout("Setup", body, active="home", flash=flash)


def render_home(flash=None, tag_filter=None):
    detailed = list_payloads_detailed()
    if tag_filter:
        detailed = [r for r in detailed if tag_filter in r[4]]
    facts = _system_facts()
    if not detailed and not tag_filter:
        body = (
            '<div class="card"><div class="empty">'
            '%s'
            '<h1>No payloads yet</h1>'
            '<p>Pick a starter template or upload a <span class="kbd">.dd</span> file.</p>'
            '<div class="row" style="justify-content:center;margin-top:14px">'
            '<a class="btn primary" href="/templates">%s Browse templates</a>'
            '<a class="btn" href="/new">%s New script</a>'
            '<a class="btn" href="/upload">%s Upload</a>'
            '</div></div></div>'
        ) % (icon("snippet", "ico ico-xl"),
             icon("template"), icon("plus"), icon("upload"))
        return layout("Payloads", body, active="home", flash=flash)

    # Gather every tag across visible payloads for filter chips
    all_tags = []
    for _, _, _, _, ts in detailed:
        for t in ts:
            if t not in all_tags:
                all_tags.append(t)

    rows = []
    for name, size, mtime, pinned, tags in detailed:
        safe = html_escape(name)
        tag_html = ''
        if tags:
            tag_html = '<div class="tags-row">' + ''.join(
                '<a class="chip" href="/?tag=%s">%s%s</a>' %
                (html_escape(t), icon("tag"), html_escape(t)) for t in tags
            ) + '</div>'
        star_icon = icon("star")
        star_cls = "active" if pinned else ""
        rows.append(
            '<tr%s data-name="%s" data-size="%d" data-mtime="%d">'
            '<td><input type="checkbox" class="checkbox row-check" value="%s"></td>'
            '<td>'
            '<form method="post" action="/pin/%s" style="display:inline;margin:0">'
            '%s<button class="icon-btn %s" type="submit" '
            'style="width:28px;height:28px" title="%s">%s</button></form>'
            '</td>'
            '<td><strong>%s</strong>%s</td>'
            '<td class="hide-sm muted">%s</td>'
            '<td><div class="actions">'
            '<a class="btn small" href="/edit/%s">%s Edit</a>'
            '<div class="dropdown"><button class="btn small ghost" type="button">%s</button>'
            '<div class="dropdown-menu">'
            '<a href="/preview/%s">%s Preview</a>'
            '<a href="/download/%s">%s Download</a>'
            '<a href="/diff/%s">%s Diff vs backup</a>'
            '<a href="/rename/%s">%s Rename</a>'
            '<a href="/cli/%s">%s CLI examples</a>'
            '<form method="post" action="/duplicate/%s" style="display:block;margin:0">'
            '%s<button type="submit">%s Duplicate</button></form>'
            '</div></div>'
            '<form method="post" action="/run/%s" style="display:inline;margin:0">'
            '%s<button class="btn small warn" type="submit" '
            'onclick="return confirm(\'Run %s? This will inject keystrokes.\')">'
            '%s</button></form>'
            '<form method="post" action="/delete/%s" style="display:inline;margin:0">'
            '%s<button class="btn small danger" type="submit" '
            'onclick="return confirm(\'Delete %s permanently?\')">%s</button></form>'
            '</div></td>'
            '</tr>'
            % (' class="pinned"' if pinned else '',
               safe, size, mtime,
               safe,
               safe, csrf_field(), star_cls,
               "Unpin" if pinned else "Pin", star_icon,
               safe, tag_html,
               format_size(size),
               safe, icon("edit"),
               icon("more"),
               safe, icon("preview"),
               safe, icon("download"),
               safe, icon("diff"),
               safe, icon("rename"),
               safe, icon("send"),
               safe, csrf_field(), icon("clone"),
               safe, csrf_field(), safe, icon("play"),
               safe, csrf_field(), safe, icon("trash"))
        )

    storage_pct = ""
    if facts["fs_total"]:
        pct = int(100 * facts["fs_used"] / facts["fs_total"])
        storage_pct = "%d%% full" % pct

    hero = (
        '<div class="hero">'
        '<div class="tile"><div class="k">%s Payloads</div><div class="n">%d</div></div>'
        '<div class="tile"><div class="k">%s Uptime</div><div class="n" style="font-size:18px">%s</div></div>'
        '<div class="tile"><div class="k">%s Free RAM</div><div class="n" style="font-size:18px">%s</div></div>'
        '<div class="tile"><div class="k">%s Storage</div><div class="n" style="font-size:18px">%s</div></div>'
        '</div>'
    ) % (
        icon("snippet"), facts["payload_count"],
        icon("system"), facts["uptime"],
        icon("info"), format_size(facts["free_mem"]),
        icon("audit"), storage_pct or format_size(facts["fs_free"]),
    )

    tag_filter_bar = ""
    if all_tags or tag_filter:
        chips = ''
        if tag_filter:
            chips += '<a class="chip active" href="/">%s clear filter</a>' % icon("close")
        for t in all_tags:
            cls = " active" if t == tag_filter else ""
            chips += '<a class="chip%s" href="/?tag=%s">%s%s</a>' % (
                cls, html_escape(t), icon("tag"), html_escape(t))
        tag_filter_bar = ('<div class="card" style="padding:12px 14px"><div class="row">'
                          '<span class="muted" style="font-size:12px">'
                          '%s Tags</span>%s</div></div>') % (icon("tag"), chips)

    body = (
        '%s'
        '<div class="bulk-bar" id="bulkBar">'
        '<span><strong id="selN">0</strong> selected</span>'
        '<div class="spacer"></div>'
        '<form method="post" action="/bulk-delete" id="bulkForm">'
        '%s<input type="hidden" name="names" id="bulkNames">'
        '<button class="btn small danger" type="submit" '
        'onclick="return confirm(\'Delete all selected payloads?\')">%s Delete selected</button>'
        '</form>'
        '<button class="btn small ghost" type="button" id="bulkClear">Clear</button>'
        '</div>'
        '<div class="card">'
        '<div class="row"><h1>Payloads</h1>'
        '<span class="badge">%d</span><div class="spacer"></div>'
        '<a class="btn primary" href="/new">%s New</a>'
        '<a class="btn" href="/templates">%s Templates</a>'
        '<a class="btn" href="/upload">%s Upload</a>'
        '<a class="btn ghost" href="/search">%s Search</a>'
        '<form method="post" action="/wipe" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'WIPE ALL payloads? This cannot be undone.\')'
        '&amp;&amp;confirm(\'Are you absolutely sure?\')">'
        '%s Wipe all</button></form>'
        '</div>'
        '%s'
        '<div class="field" style="margin-top:14px">'
        '<input id="filter" class="input" placeholder="Filter by name or tag...">'
        '</div>'
        '<table id="ptable"><thead><tr>'
        '<th style="width:30px"><input type="checkbox" class="checkbox" id="selAll"></th>'
        '<th style="width:30px"></th>'
        '<th class="sortable" data-sort="name">Name</th>'
        '<th class="sortable hide-sm" data-sort="size">Size</th>'
        '<th style="text-align:right">Actions</th></tr></thead>'
        '<tbody id="rows">%s</tbody></table></div>'
        '<script>'
        'var bf=document.getElementById("filter"),'
        'rs=document.querySelectorAll("#rows tr");'
        'bf.addEventListener("input",function(){'
        'var q=bf.value.toLowerCase();'
        'rs.forEach(function(r){r.style.display=r.innerText.toLowerCase().indexOf(q)>-1?"":"none"})});'
        # Sort
        'var sortDir={};'
        'document.querySelectorAll("th.sortable").forEach(function(th){'
        'th.addEventListener("click",function(){'
        'var key=th.dataset.sort,dir=sortDir[key]==="asc"?"desc":"asc";sortDir={};sortDir[key]=dir;'
        'document.querySelectorAll("th.sortable").forEach(function(t){t.classList.remove("asc","desc")});'
        'th.classList.add(dir);'
        'var tbody=document.getElementById("rows");'
        'var arr=Array.from(tbody.children);'
        'arr.sort(function(a,b){'
        'var av=key==="size"?+a.dataset.size:a.dataset.name.toLowerCase();'
        'var bv=key==="size"?+b.dataset.size:b.dataset.name.toLowerCase();'
        'if(av<bv)return dir==="asc"?-1:1;if(av>bv)return dir==="asc"?1:-1;return 0});'
        'arr.forEach(function(r){tbody.appendChild(r)});'
        '})});'
        # Bulk select
        'var bb=document.getElementById("bulkBar"),sa=document.getElementById("selAll"),'
        'sN=document.getElementById("selN"),bn=document.getElementById("bulkNames"),'
        'bc=document.getElementById("bulkClear");'
        'function updateBulk(){'
        'var sel=Array.from(document.querySelectorAll(".row-check:checked")).map(function(c){return c.value});'
        'sN.innerText=sel.length;bn.value=sel.join(",");'
        'bb.classList.toggle("open",sel.length>0)'
        '}'
        'document.querySelectorAll(".row-check").forEach(function(c){c.addEventListener("change",updateBulk)});'
        'sa.addEventListener("change",function(){document.querySelectorAll(".row-check").forEach(function(c){c.checked=sa.checked});updateBulk()});'
        'bc.addEventListener("click",function(){document.querySelectorAll(".row-check").forEach(function(c){c.checked=false});sa.checked=false;updateBulk()});'
        '</script>'
    ) % (hero, csrf_field(), icon("trash"),
         len(detailed),
         icon("plus"), icon("template"), icon("upload"), icon("search"),
         csrf_field(), icon("trash"),
         tag_filter_bar,
         ''.join(rows))
    return layout("Payloads", body, active="home", flash=flash)


def render_editor(filename, contents, flash=None, warnings=None):
    safe = html_escape(filename)
    has_backup = file_exists(filename + ".bak")
    restore_html = ""
    if has_backup:
        restore_html = (
            '<form method="post" action="/restore/%s" style="display:inline">'
            '%s<button class="btn ghost" type="submit" '
            'onclick="return confirm(\'Restore the previous version? Current contents will be backed up.\')">'
            '%s Restore previous</button></form> '
            '<a class="btn ghost" href="/diff/%s">%s View diff</a>'
        ) % (safe, csrf_field(), icon("back"), safe, icon("diff"))
    snip_buttons = ''.join(
        '<button type="button" class="snip" data-snip="%s">%s</button>'
        % (html_escape(code).replace('\n', '\\n'), html_escape(label))
        for label, code in SNIPPETS
    )
    warn_html = ""
    if warnings:
        items = ''.join('<li>%s</li>' % html_escape(w) for w in warnings)
        warn_html = ('<div class="alert warn"><strong>%s Lint warnings</strong>'
                     '<ul class="warn-list">%s</ul></div>') % (
            icon("warn"), items)
    # Find & replace modal (inline in editor only)
    find_modal = (
        '<div class="modal-back" id="frModal">'
        '<div class="modal" role="dialog" aria-modal="true">'
        '<div class="row"><h2>%s Find &amp; replace</h2><div class="spacer"></div>'
        '<button class="icon-btn" data-close-modal title="Close">%s</button></div>'
        '<div class="field"><label>Find</label>'
        '<input class="input" id="frFind" autocomplete="off"></div>'
        '<div class="field"><label>Replace with</label>'
        '<input class="input" id="frRep" autocomplete="off"></div>'
        '<div class="row">'
        '<button class="btn" type="button" id="frNext">Find next</button>'
        '<button class="btn" type="button" id="frReplace">Replace</button>'
        '<button class="btn primary" type="button" id="frAll">Replace all</button>'
        '<span class="spacer"></span>'
        '<label class="row" style="gap:6px;margin:0;font-size:13px">'
        '<input type="checkbox" id="frCase"> Case-sensitive</label>'
        '</div>'
        '</div></div>'
    ) % (icon("find", "ico ico-lg"), icon("close"))
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s Editing %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/preview/%s">%s Preview</a>'
        '<a class="btn ghost" href="/cli/%s">%s CLI</a>'
        '<a class="btn" href="/">%s Back</a></div>'
        '%s'
        '<form method="post" action="/write/%s" id="ed">'
        '%s'
        '<div class="field">'
        '<label>Script <span class="shortcut">'
        '<span class="kbd">Ctrl</span>+<span class="kbd">S</span> save '
        '&middot; <span class="kbd">Ctrl</span>+<span class="kbd">F</span> find</span></label>'
        '<div class="editor-wrap">'
        '<pre class="line-nums" id="lns" aria-hidden="true">1</pre>'
        '<textarea name="scriptData" id="ta" data-name="%s" spellcheck="false">%s</textarea>'
        '</div>'
        '<p class="muted" id="stats" style="margin:6px 0 0 0;font-size:12px">'
        'Max %s. Tip: click a snippet below to insert; press <span class="kbd">?</span> for shortcuts.</p>'
        '</div>'
        '<div class="sticky-bar">'
        '<button class="btn primary" type="submit">%s Save</button>'
        '<button class="btn" type="button" id="frBtn">%s Find</button>'
        '<a class="btn" href="/download/%s">%s Download</a>'
        '<a class="btn" href="/rename/%s">%s Rename</a>'
        '%s'
        '<button type="button" class="btn ghost" id="restoreBtn" style="display:none">'
        '%s Restore unsaved draft</button>'
        '</div></form></div>'
        '<div class="card"><h3>%s Snippets</h3>'
        '<div class="snippets">%s</div></div>'
        '%s'
        '<script>'
        'var ta=document.getElementById("ta"),st=document.getElementById("stats"),'
        'rb=document.getElementById("restoreBtn"),lns=document.getElementById("lns"),'
        'dk="pd:draft:"+ta.dataset.name;'
        'function upd(){var lc=ta.value.split("\\n").length,b=ta.value.length;'
        'st.innerText=lc+" lines \\u00B7 "+b+" bytes";'
        'var s="";for(var i=1;i<=lc;i++)s+=i+"\\n";lns.textContent=s;'
        'try{localStorage.setItem(dk,ta.value)}catch(e){}}'
        'ta.addEventListener("input",upd);'
        'ta.addEventListener("scroll",function(){lns.scrollTop=ta.scrollTop});'
        'upd();'
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
        # Ctrl+S / Ctrl+F
        'document.addEventListener("keydown",function(e){'
        'if((e.ctrlKey||e.metaKey)&&e.key==="s"){e.preventDefault();document.getElementById("ed").submit()}'
        'if((e.ctrlKey||e.metaKey)&&(e.key==="f"||e.key==="h")){e.preventDefault();openFR()}'
        '});'
        'document.getElementById("ed").addEventListener("submit",function(){'
        'try{localStorage.removeItem(dk)}catch(e){}});'
        # Find & replace
        'var fr=document.getElementById("frModal"),fb=document.getElementById("frBtn"),'
        'ff=document.getElementById("frFind"),fp=document.getElementById("frRep"),'
        'fc=document.getElementById("frCase");'
        'function openFR(){fr.classList.add("open");setTimeout(function(){ff.focus()},50)}'
        'fb.addEventListener("click",openFR);'
        'function findNext(){var q=ff.value;if(!q)return;'
        'var hay=fc.checked?ta.value:ta.value.toLowerCase();'
        'var needle=fc.checked?q:q.toLowerCase();'
        'var from=ta.selectionEnd||0;var i=hay.indexOf(needle,from);'
        'if(i<0)i=hay.indexOf(needle,0);'
        'if(i>=0){ta.focus();ta.setSelectionRange(i,i+q.length)}}'
        'document.getElementById("frNext").addEventListener("click",findNext);'
        'document.getElementById("frReplace").addEventListener("click",function(){'
        'var sel=ta.value.substring(ta.selectionStart,ta.selectionEnd);'
        'var needle=ff.value;if(!needle)return;'
        'var match=fc.checked?sel===needle:sel.toLowerCase()===needle.toLowerCase();'
        'if(match){var s=ta.selectionStart;ta.setRangeText(fp.value,s,ta.selectionEnd,"end");upd()}'
        'findNext()});'
        'document.getElementById("frAll").addEventListener("click",function(){'
        'var needle=ff.value;if(!needle)return;'
        'if(fc.checked){ta.value=ta.value.split(needle).join(fp.value)}'
        'else{var re=new RegExp(needle.replace(/[.*+?^${}()|[\\]\\\\]/g,"\\\\$&"),"gi");'
        'ta.value=ta.value.replace(re,fp.value)}'
        'upd()});'
        '</script>'
    ) % (icon("edit", "ico ico-lg"), safe,
         safe, icon("preview"),
         safe, icon("send"),
         icon("back"),
         warn_html,
         safe, csrf_field(),
         safe, html_escape(contents),
         format_size(MAX_PAYLOAD_BYTES),
         icon("save"), icon("find"),
         safe, icon("download"),
         safe, icon("rename"),
         restore_html,
         icon("back"),
         icon("snippet"), snip_buttons,
         find_modal)
    return layout("Edit " + filename, body, active="home", flash=flash)


def render_new(flash=None, prefill_name="", prefill_data=""):
    body = (
        '<div class="card"><h1>%s New script</h1>'
        '<form method="post" action="/new">'
        '%s'
        '<div class="field"><label>Filename</label>'
        '<input class="input" name="scriptName" '
        'placeholder="my_payload.dd" value="%s" required></div>'
        '<div class="field"><label>Script</label>'
        '<textarea name="scriptData" placeholder="REM Your DuckyScript here">'
        '%s</textarea></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">%s Create</button>'
        '<a class="btn" href="/">%s Cancel</a>'
        '<a class="btn ghost" href="/templates">%s Templates</a>'
        '<a class="btn ghost" href="/snippets">%s Snippets</a>'
        '</div></form></div>'
    ) % (icon("plus", "ico ico-lg"), csrf_field(),
         html_escape(prefill_name), html_escape(prefill_data),
         icon("plus"), icon("back"), icon("template"), icon("snippet"))
    return layout("New script", body, active="new", flash=flash)


def render_upload(flash=None):
    body = (
        '<div class="card"><h1>%s Upload payload</h1>'
        '<p class="muted">Drop a local <span class="kbd">.dd</span> file '
        'below or click to choose. It is read entirely in your browser '
        'and posted as text to the device.</p>'
        '<input type="file" id="file" accept=".dd,.txt,text/plain" '
        'style="display:none">'
        '<div class="dropzone" id="dz">'
        '%s'
        '<div><strong>Drop file here or click to browse</strong></div>'
        '<div class="muted">Plain-text DuckyScript only</div>'
        '<div class="name" id="dzName"></div>'
        '</div>'
        '<div class="field" style="margin-top:14px"><label>Save as</label>'
        '<input class="input" id="name" placeholder="payload.dd"></div>'
        '<form method="post" action="/new" id="upf">'
        '%s'
        '<input type="hidden" name="scriptName" id="hname">'
        '<textarea name="scriptData" id="hdata" style="display:none"></textarea>'
        '<div class="row">'
        '<button class="btn primary" type="submit" id="go" disabled>%s Upload</button>'
        '<a class="btn" href="/">%s Cancel</a></div></form></div>'
        '<script>'
        'var fi=document.getElementById("file"),'
        'na=document.getElementById("name"),'
        'hn=document.getElementById("hname"),'
        'hd=document.getElementById("hdata"),'
        'go=document.getElementById("go"),'
        'dz=document.getElementById("dz"),'
        'dn=document.getElementById("dzName");'
        'function ck(){go.disabled=!(hd.value&&(na.value||""))}'
        'function handle(file){'
        'if(!file)return;'
        'if(!na.value)na.value=file.name;'
        'dn.innerText=file.name+"  ("+file.size+" B)";'
        'var r=new FileReader();r.onload=function(){hd.value=r.result;ck()};'
        'r.readAsText(file);'
        '}'
        'dz.addEventListener("click",function(){fi.click()});'
        'fi.addEventListener("change",function(){handle(fi.files[0])});'
        'na.addEventListener("input",ck);'
        '["dragenter","dragover"].forEach(function(e){'
        'dz.addEventListener(e,function(ev){ev.preventDefault();dz.classList.add("over")})});'
        '["dragleave","drop"].forEach(function(e){'
        'dz.addEventListener(e,function(ev){ev.preventDefault();dz.classList.remove("over")})});'
        'dz.addEventListener("drop",function(ev){handle(ev.dataTransfer.files[0])});'
        'document.getElementById("upf").addEventListener("submit",function(){'
        'hn.value=na.value});'
        '</script>'
    ) % (icon("upload", "ico ico-lg"), icon("upload", "ico ico-lg"),
         csrf_field(), icon("upload"), icon("back"))
    return layout("Upload", body, active="upload", flash=flash)


def render_snippets(flash=None):
    items = ''
    for label, code in SNIPPETS:
        items += (
            '<div class="card"><div class="row">'
            '<h2>%s</h2><div class="spacer"></div>'
            '<button class="btn small" onclick="copyText(this)">%s Copy</button>'
            '</div>'
            '<pre class="code-preview" style="margin:8px 0 0">'
            '<code>%s</code></pre></div>'
        ) % (html_escape(label), icon("copy"), html_escape(code))
    body = (
        '<div class="card"><h1>%s DuckyScript snippets</h1>'
        '<p class="muted">Common building blocks. Click <em>Copy</em>, or '
        'use them from inside the editor sidebar.</p></div>%s'
        '<script>'
        'function copyText(b){var c=b.parentElement.parentElement'
        '.querySelector("code").innerText;'
        'if(navigator.clipboard){navigator.clipboard.writeText(c);'
        'b.innerText="Copied"}else{b.innerText="Select manually"}}'
        '</script>'
    ) % (icon("wand", "ico ico-lg"), items)
    return layout("Snippets", body, active="snippets", flash=flash)


def render_templates(flash=None):
    cards = ''
    for t in TEMPLATES:
        cards += (
            '<a class="template-card" href="/new?template=%s">'
            '<div class="ico-host">%s</div>'
            '<div class="body">'
            '<h2>%s</h2>'
            '<p>%s</p>'
            '<div style="margin-top:6px">%s</div>'
            '</div></a>'
        ) % (
            html_escape(t["key"]),
            icon("template", "ico ico-lg"),
            html_escape(t["name"]),
            html_escape(t["desc"]),
            ''.join('<span class="chip">%s%s</span>' % (icon("tag"), html_escape(tag.strip()))
                    for tag in t["tags"].split(",") if tag.strip())
        )
    body = (
        '<div class="card"><h1>%s Templates</h1>'
        '<p class="muted">Pre-built starters. Pick one to open the editor pre-filled — '
        'edit, save, run.</p></div>'
        '<div>%s</div>'
    ) % (icon("template", "ico ico-lg"), cards)
    return layout("Templates", body, active="templates", flash=flash)


def render_preview(filename, contents):
    safe = html_escape(filename)
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s Preview: %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/edit/%s">%s Edit</a>'
        '<a class="btn" href="/">%s Back</a></div>'
        '<pre class="code-preview">%s</pre></div>'
    ) % (icon("preview", "ico ico-lg"), safe, safe,
         icon("edit"), icon("back"), highlight_payload(contents))
    return layout("Preview " + filename, body, active="home")


def render_rename(filename, flash=None):
    safe = html_escape(filename)
    body = (
        '<div class="card"><h1>%s Rename %s</h1>'
        '<form method="post" action="/rename/%s">'
        '%s'
        '<div class="field"><label>New name</label>'
        '<input class="input" name="newName" value="%s" required></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">%s Rename</button>'
        '<a class="btn" href="/">%s Cancel</a></div></form></div>'
    ) % (icon("rename", "ico ico-lg"), safe, safe, csrf_field(), safe,
         icon("rename"), icon("back"))
    return layout("Rename " + filename, body, active="home", flash=flash)


def render_diff(filename, old, new, flash=None):
    safe = html_escape(filename)
    rows = render_diff_lines(old, new)
    if not rows:
        body_html = '<div class="empty"><p>No differences.</p></div>'
    else:
        body_lines = []
        for kind, ln, txt in rows:
            sign = "+" if kind == "add" else ("-" if kind == "del" else " ")
            body_lines.append(
                '<div class="diff-row %s">'
                '<div class="gutter">%s %d</div><div>%s</div></div>'
                % (kind, sign, ln, html_escape(txt))
            )
        body_html = '<div class="code-preview" style="white-space:normal">' + ''.join(body_lines) + '</div>'
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s Diff: %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/edit/%s">%s Edit</a>'
        '<a class="btn" href="/">%s Back</a></div>'
        '<p class="muted">Comparing the saved version with the backup '
        '(<span class="kbd">%s.bak</span>).</p>%s</div>'
    ) % (icon("diff", "ico ico-lg"), safe, safe,
         icon("edit"), icon("back"), safe, body_html)
    return layout("Diff " + filename, body, active="home", flash=flash)


def render_cli(filename, request_host="192.168.4.1"):
    safe = html_escape(filename)
    user = WEB_USERNAME or "USER"
    base = "http://%s" % request_host
    cmd_list = (
        "# List payloads\n"
        "curl -u %s:PASSWORD %s/api/payloads\n\n"
        "# Run this payload via the friendly route (requires CSRF; easier to use the API)\n"
        "curl -u %s:PASSWORD %s/run/%s\n\n"
        "# Download this payload\n"
        "curl -u %s:PASSWORD %s/download/%s -o %s\n\n"
        "# Quick health probe (no auth)\n"
        "curl %s/health\n"
    ) % (user, base, user, base, filename,
         user, base, filename, filename, base)
    api_token_block = (
        "# With API token (preferred for automation)\n"
        "curl -H 'Authorization: Bearer YOUR_TOKEN' %s/api/system\n"
        "curl -X GET %s/api/run/1?token=YOUR_TOKEN\n"
    ) % (base, base)
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s CLI examples: %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/edit/%s">%s Edit</a>'
        '<a class="btn" href="/">%s Back</a></div>'
        '<p class="muted">Replace <span class="kbd">PASSWORD</span> with your web password '
        'and <span class="kbd">YOUR_TOKEN</span> with the API token from Settings.</p>'
        '<h3>Web-auth examples</h3>'
        '<pre class="code-preview"><code>%s</code></pre>'
        '<h3>API-token examples</h3>'
        '<pre class="code-preview"><code>%s</code></pre>'
        '<button class="btn" type="button" onclick="copy(this)">%s Copy all</button>'
        '</div>'
        '<script>'
        'function copy(b){var t="";document.querySelectorAll("code").forEach(function(c){t+=c.innerText+"\\n"});'
        'if(navigator.clipboard){navigator.clipboard.writeText(t);b.innerText="Copied"}}'
        '</script>'
    ) % (icon("send", "ico ico-lg"), safe, safe,
         icon("edit"), icon("back"),
         html_escape(cmd_list), html_escape(api_token_block), icon("copy"))
    return layout("CLI " + filename, body, active="home")


def render_search(query="", results=None, flash=None):
    results = results or []
    res_html = ""
    if query and not results:
        res_html = ('<div class="card empty">%s'
                    '<p>No matches for <strong>%s</strong>.</p></div>'
                    ) % (icon("search", "ico ico-xl"), html_escape(query))
    elif results:
        rows = ''
        for name, lineno, line in results:
            ql = query.lower()
            li = line.lower()
            idx = li.find(ql)
            if idx >= 0:
                marked = (html_escape(line[:idx])
                          + '<mark>' + html_escape(line[idx:idx + len(query)]) + '</mark>'
                          + html_escape(line[idx + len(query):]))
            else:
                marked = html_escape(line)
            rows += (
                '<div class="search-result">'
                '<a href="/edit/%s">%s</a> '
                '<span class="muted">:%d</span>'
                '<div class="line">%s</div></div>'
            ) % (html_escape(name), html_escape(name), lineno, marked)
        res_html = '<div class="card"><h2>%d match%s</h2>%s</div>' % (
            len(results), '' if len(results) == 1 else 'es', rows)
    body = (
        '<div class="card"><h1>%s Search payloads</h1>'
        '<form method="get" action="/search">'
        '<div class="row">'
        '<input id="q" class="input" name="q" placeholder="Search inside payload text..." '
        'value="%s" autofocus>'
        '<button class="btn primary" type="submit">%s Search</button>'
        '</div></form>'
        '<p class="muted" style="margin-top:8px;font-size:12px">'
        'Plain substring search; case-insensitive. First 100 matches. '
        'Press <span class="kbd">/</span> to focus the box.</p>'
        '</div>%s'
        '<script>'
        'document.addEventListener("keydown",function(e){'
        'var tag=(e.target&&e.target.tagName)||"";'
        'if(e.key==="/"&&tag!=="INPUT"&&tag!=="TEXTAREA"){'
        'e.preventDefault();document.getElementById("q").focus()}'
        '});'
        '</script>'
    ) % (icon("search", "ico ico-lg"), html_escape(query),
         icon("search"), res_html)
    return layout("Search", body, active="search", flash=flash)


def render_audit(flash=None):
    tail = read_audit_tail()
    if not tail:
        tail = "(empty)"
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s Audit log</h1><div class="spacer"></div>'
        '<form method="post" action="/audit/clear" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Clear audit log?\')">%s Clear log</button>'
        '</form></div>'
        '<p class="muted">Most recent entries (truncated to last 12 KB). '
        'Times are seconds since boot.</p>'
        '<pre class="audit">%s</pre></div>'
    ) % (icon("audit", "ico ico-lg"), csrf_field(), icon("trash"),
         html_escape(tail))
    return layout("Audit", body, active="audit", flash=flash)


def render_system(flash=None):
    f = _system_facts()
    cpu = "?" if f["cpu_temp_c"] is None else ("%.1f \xb0C" % f["cpu_temp_c"])
    stats = [
        ("Board", f["board"]),
        ("AP SSID", f["ap_ssid"]),
        ("Access Point IP", f["ap_ip"]),
        ("Access Point MAC", f["ap_mac"]),
        ("Channel", f["ap_channel"]),
        ("Uptime", f["uptime"]),
        ("Payloads", str(f["payload_count"])),
        ("Connected stations",
         "?" if f["stations_connected"] is None else str(f["stations_connected"])),
        ("Free RAM", format_size(f["free_mem"])),
        ("Used RAM", format_size(f["used_mem"])),
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
    bar_html = ""
    if f["fs_total"]:
        pct = int(100 * f["fs_used"] / f["fs_total"])
        bar_html = (
            '<div class="card"><h3>%s Storage</h3>'
            '<div class="bar"><div style="width:%d%%"></div></div>'
            '<div class="row" style="margin-top:8px;font-size:13px;color:var(--muted)">'
            '<span>%s used of %s</span><div class="spacer"></div>'
            '<span>%s free</span></div></div>'
        ) % (icon("audit"), pct,
             format_size(f["fs_used"]), format_size(f["fs_total"]),
             format_size(f["fs_free"]))
    body = (
        '<div class="card"><div class="row">'
        '<h1>%s System</h1><div class="spacer"></div>'
        '<label class="row" style="gap:6px;margin:0;font-size:13px;color:var(--muted)">'
        '<input type="checkbox" id="autoR"> Auto-refresh</label>'
        '</div>'
        '<p class="muted">Live status of the device.</p>'
        '<div class="grid">%s</div>'
        '<div class="row" style="margin-top:16px">'
        '<a class="btn" href="/system">%s Refresh</a>'
        '<a class="btn ghost" href="/api/system">%s JSON</a>'
        '<a class="btn ghost" href="/health">%s Health</a>'
        '<a class="btn ghost" href="/settings">%s Settings</a>'
        '<form method="post" action="/system/reboot" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Reboot the Pico now?\')">%s Reboot</button>'
        '</form></div></div>'
        '%s'
        '<script>'
        'var ar=document.getElementById("autoR"),iv;'
        'try{ar.checked=localStorage.getItem("pdAuto")==="1"}catch(e){}'
        'function tick(){location.reload()}'
        'function setup(){if(iv)clearInterval(iv);if(ar.checked)iv=setInterval(tick,5000)}'
        'ar.addEventListener("change",function(){try{localStorage.setItem("pdAuto",ar.checked?"1":"0")}catch(e){};setup()});'
        'setup();'
        '</script>'
    ) % (icon("system", "ico ico-lg"), cards,
         icon("refresh"), icon("info"), icon("ok"), icon("settings"),
         csrf_field(), icon("reboot"), bar_html)
    return layout("System", body, active="system", flash=flash)


def render_settings(flash=None):
    f = _system_facts()
    body = (
        '<div class="card"><h1>%s Settings</h1>'
        '<p class="muted">Change credentials, manage the API token, '
        'tune the look, or wipe everything.</p></div>'
        # Appearance
        '<div class="card"><h2>%s Appearance</h2>'
        '<div class="row" style="gap:10px;margin-top:10px">'
        '<span class="muted">Accent</span>'
        '%s'
        '</div>'
        '<p class="muted" style="margin-top:10px;font-size:13px">'
        'Theme cycles dark → light → OLED via the %s button in the header.</p>'
        '<script>'
        '(function(){'
        'document.querySelectorAll(".accent-swatch").forEach(function(s){'
        'var cur=document.documentElement.getAttribute("data-accent")||"cyan";'
        'if(s.dataset.c===cur)s.classList.add("active");'
        's.addEventListener("click",function(){'
        'document.querySelectorAll(".accent-swatch").forEach(function(o){o.classList.remove("active")});'
        's.classList.add("active");'
        'document.documentElement.setAttribute("data-accent",s.dataset.c);'
        'try{localStorage.setItem("pdaccent",s.dataset.c)}catch(e){}'
        '})});'
        '})();'
        '</script>'
        '</div>'
        # Change password
        '<div class="card"><h2>%s Change password</h2>'
        '<form method="post" action="/settings/password">'
        '%s'
        '<div class="field"><label>Current password</label>'
        '<input class="input" type="password" name="current" minlength="8" maxlength="64" required></div>'
        '<div class="field"><label>New password (min 8 chars)</label>'
        '<input class="input" type="password" name="new" minlength="8" maxlength="64" required></div>'
        '<div class="field"><label>Confirm new password</label>'
        '<input class="input" type="password" name="new2" minlength="8" maxlength="64" required></div>'
        '<div class="row"><button class="btn primary" type="submit">%s Update password</button></div>'
        '</form></div>'
        # API token
        '<div class="card"><h2>%s API token</h2>'
        '<p class="muted">Currently: <strong>%s</strong></p>'
        '<div class="row" style="margin-top:10px">'
        '<form method="post" action="/settings/rotate-token" style="display:inline">'
        '%s<button class="btn" type="submit" '
        'onclick="return confirm(\'Generate a new API token? Existing scripts will break.\')">%s Rotate token</button>'
        '</form>'
        '<form method="post" action="/settings/clear-token" style="display:inline">'
        '%s<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Disable the API token entirely?\')">%s Clear token</button>'
        '</form></div></div>'
        # Factory reset
        '<div class="card"><h2>%s Danger zone</h2>'
        '<p class="muted">Factory reset wipes <em>everything</em>: payloads, '
        'backups, audit log, pins, and credentials. The device returns to '
        'first-run setup.</p>'
        '<form method="post" action="/settings/factory-reset" style="margin-top:10px">'
        '%s'
        '<div class="field"><label>Type <span class="kbd">RESET</span> to confirm</label>'
        '<input class="input" name="confirm" required></div>'
        '<div class="row"><button class="btn danger" type="submit">%s Factory reset</button></div>'
        '</form></div>'
    ) % (
        icon("settings", "ico ico-lg"),
        icon("wand"),
        ''.join('<button type="button" class="accent-swatch" data-c="%s" title="%s"></button>' % (c, c)
                for c in ACCENT_THEMES),
        icon("sun"),
        icon("lock"), csrf_field(), icon("ok"),
        icon("shield"),
        "set (rotate or clear below)" if f["api_token_set"] else "not set",
        csrf_field(), icon("refresh"),
        csrf_field(), icon("trash"),
        icon("warn"), csrf_field(), icon("trash"),
    )
    return layout("Settings", body, active="settings", flash=flash)


# ===========================================================================
# WSGI app
# ===========================================================================
web_app = WSGIApp()


def _setup_gate(request, path):
    if SETUP_REQUIRED and not path.startswith("/setup") \
            and not path.startswith("/api/") and path != "/health":
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


# ---- Setup ----
@web_app.route("/setup", methods=['GET', 'POST'])
def setup_route(request):
    if not SETUP_REQUIRED:
        return redirect("/")
    if request.method == 'GET':
        return html_response(render_setup(flash=get_flash(request)))
    try:
        form = parse_form_single(request_body(request))
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
            flash=("err", "Username must be 3-32 characters."), prefill_user=user))
    if len(pw) < 8:
        return html_response(render_setup(
            flash=FLASH_MESSAGES["weak_pw"], prefill_user=user))
    if pw != pw2:
        return html_response(render_setup(
            flash=FLASH_MESSAGES["pw_mismatch"], prefill_user=user))
    if token and len(token) < 16:
        return html_response(render_setup(
            flash=("err", "API token should be at least 16 characters."),
            prefill_user=user))
    try:
        apply_credentials(user, pw, token)
    except Exception as ex:
        print("setup write failed:", ex)
        return html_response(render_setup(
            flash=("err", "Could not write credentials."), prefill_user=user))
    audit("setup.complete", "user=%s api=%s" % (user, "yes" if token else "no"))
    return redirect("/", flash="setup_done")


# ---- Home ----
@web_app.route("/")
def index(request):
    gate = _setup_gate(request, "/")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    tag = query_get(request, 'tag')
    return html_response(render_home(flash=get_flash(request), tag_filter=tag))


@web_app.route("/ducky")
def ducky_compat(request):
    return index(request)


# ---- New (with optional ?template=key) ----
@web_app.route("/new", methods=['GET', 'POST'])
def new_script(request):
    gate = _setup_gate(request, "/new")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    if request.method == 'GET':
        prefill_name = ""
        prefill_data = ""
        tmpl_key = query_get(request, 'template')
        if tmpl_key:
            t = template_by_key(tmpl_key)
            if t:
                prefill_name = t["key"] + ".dd"
                prefill_data = t["body"]
        return html_response(render_new(flash=get_flash(request),
                                        prefill_name=prefill_name,
                                        prefill_data=prefill_data))
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/new", flash="too_big")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    name = normalize_payload_name(form.get('scriptName', ''))
    if not name:
        return html_response(render_new(
            flash=("err", "Invalid filename."),
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
        write_file_safely(name, data, backup=False)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/new", flash="bad_request")
    audit("payload.create", name)
    return redirect("/edit/" + name, flash="saved")


# ---- Edit / Write / Restore ----
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
    return html_response(render_editor(name, contents, flash=get_flash(request)))


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
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/", flash="too_big")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    data = form.get('scriptData', '')
    if len(data) > MAX_PAYLOAD_BYTES:
        return redirect("/edit/" + name, flash="too_big")
    try:
        write_file_safely(name, data, backup=True)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/edit/" + name, flash="bad_request")
    audit("payload.save", "%s %dB" % (name, len(data)))
    warnings = lint_payload(data)
    if warnings:
        return html_response(render_editor(name, data,
                                           flash=("ok", "Saved with warnings."),
                                           warnings=warnings))
    return redirect("/edit/" + name, flash="saved")


@web_app.route("/restore/<filename>", methods=['POST'])
def restore_route(request, filename):
    gate = _setup_gate(request, "/restore")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name:
        return redirect("/", flash="bad_name")
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    bak = name + ".bak"
    if not file_exists(bak):
        return redirect("/edit/" + name, flash="no_backup")
    try:
        with open(bak, "r") as f:
            prev = f.read()
        write_file_safely(name, prev, backup=True)
    except Exception as ex:
        print("restore failed:", ex)
        return redirect("/edit/" + name, flash="bad_request")
    audit("payload.restore", name)
    return redirect("/edit/" + name, flash="restored")


# ---- Diff ----
@web_app.route("/diff/<filename>")
def diff_route(request, filename):
    gate = _setup_gate(request, "/diff")
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
            new = f.read()
    except OSError:
        return redirect("/", flash="not_found")
    bak = name + ".bak"
    old = ""
    if file_exists(bak):
        try:
            with open(bak, "r") as f:
                old = f.read()
        except OSError:
            pass
    return html_response(render_diff(name, old, new))


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
        form = parse_form_single(request_body(request))
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
        pins = read_pins()
        if name in pins:
            pins.discard(name); write_pins(pins)
    except Exception as ex:
        print("delete failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.delete", name)
    return redirect("/", flash="deleted")


# ---- Bulk delete ----
@web_app.route("/bulk-delete", methods=['POST'])
def bulk_delete_route(request):
    gate = _setup_gate(request, "/bulk-delete")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    raw_names = form.get('names', '')
    names = [n.strip() for n in raw_names.split(',') if n.strip()]
    pins = read_pins()
    removed = 0
    for n in names:
        sf = safe_filename(n)
        if sf and file_exists(sf):
            try:
                delete_file_safely(sf)
                pins.discard(sf)
                removed += 1
            except Exception:
                pass
    write_pins(pins)
    audit("payload.bulk_delete", "%d removed" % removed)
    return redirect("/", flash="bulk_done")


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
        form = parse_form_single(request_body(request))
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
    for i in range(1, 100):
        candidate = "%s_copy%s" % (base, PAYLOAD_EXT) if i == 1 \
                    else "%s_copy%d%s" % (base, i, PAYLOAD_EXT)
        if not file_exists(candidate) and len(candidate) <= MAX_FILENAME_LEN:
            new_name = candidate
            break
    if not new_name:
        return redirect("/", flash="bad_request")
    try:
        with open(name, "r") as f:
            data = f.read()
        write_file_safely(new_name, data, backup=False)
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
        form = parse_form_single(request_body(request))
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
        if file_exists(name + ".bak"):
            try:
                rename_file_safely(name + ".bak", new_name + ".bak")
            except Exception:
                pass
        # Move pin if present
        pins = read_pins()
        if name in pins:
            pins.discard(name); pins.add(new_name); write_pins(pins)
    except Exception as ex:
        print("rename failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.rename", "%s -> %s" % (name, new_name))
    return redirect("/", flash="renamed")


# ---- Pin / unpin ----
@web_app.route("/pin/<filename>", methods=['POST'])
def pin_route(request, filename):
    gate = _setup_gate(request, "/pin")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    action = toggle_pin(name)
    audit("payload.pin", "%s %s" % (name, action))
    return redirect("/", flash="pinned" if action == "on" else "unpinned")


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


# ---- Preview / CLI ----
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


@web_app.route("/cli/<filename>")
def cli_route(request, filename):
    gate = _setup_gate(request, "/cli")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    host_hdr = get_header(request, "Host") or "192.168.4.1"
    return html_response(render_cli(name, request_host=host_hdr))


# ---- Upload / Snippets / Templates / Search / Audit ----
@web_app.route("/upload")
def upload_page(request):
    gate = _setup_gate(request, "/upload")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_upload(flash=get_flash(request)))


@web_app.route("/snippets")
def snippets_page(request):
    gate = _setup_gate(request, "/snippets")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_snippets(flash=get_flash(request)))


@web_app.route("/templates")
def templates_page(request):
    gate = _setup_gate(request, "/templates")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_templates(flash=get_flash(request)))


@web_app.route("/search")
def search_page(request):
    gate = _setup_gate(request, "/search")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    q = (query_get(request, 'q') or '').strip()
    results = []
    if q:
        ql = q.lower()
        for name, _, _ in list_payloads():
            try:
                with open(name, 'r') as f:
                    for lineno, line in enumerate(f, 1):
                        if ql in line.lower():
                            line = line.rstrip("\r\n")
                            if len(line) > 200:
                                line = line[:200] + "…"
                            results.append((name, lineno, line))
                            if len(results) >= 100:
                                break
            except OSError:
                continue
            if len(results) >= 100:
                break
    return html_response(render_search(q, results, flash=get_flash(request)))


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
        form = parse_form_single(request_body(request))
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


# ---- Settings ----
@web_app.route("/settings")
def settings_page(request):
    gate = _setup_gate(request, "/settings")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    return html_response(render_settings(flash=get_flash(request)))


@web_app.route("/settings/password", methods=['POST'])
def settings_password(request):
    gate = _setup_gate(request, "/settings/password")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/settings", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    cur = form.get('current', '')
    new = form.get('new', '')
    new2 = form.get('new2', '')
    if not _ct_equals(cur, WEB_PASSWORD):
        return redirect("/settings", flash="wrong_pw")
    if len(new) < 8:
        return redirect("/settings", flash="weak_pw")
    if new != new2:
        return redirect("/settings", flash="pw_mismatch")
    try:
        apply_credentials(WEB_USERNAME, new, API_TOKEN)
    except Exception as ex:
        print("password change failed:", ex)
        return redirect("/settings", flash="bad_request")
    audit("auth.pw_changed")
    # Force re-auth: send 401 with a different realm to drop cached basic auth
    body = layout("Password changed",
                  '<div class="card"><h1>%s Password changed</h1>'
                  '<p class="muted">Sign in again to continue.</p></div>'
                  % icon("ok", "ico ico-lg"))
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky - reauth"')],
            body)


@web_app.route("/settings/rotate-token", methods=['POST'])
def settings_rotate_token(request):
    gate = _setup_gate(request, "/settings/rotate-token")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/settings", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    try:
        new_token = rotate_api_token()
    except Exception as ex:
        print("token rotate failed:", ex)
        return redirect("/settings", flash="bad_request")
    audit("api.token_rotated")
    # Show the new token once on a confirmation card
    body = layout("Token rotated",
                  '<div class="card"><h1>%s New API token generated</h1>'
                  '<p class="muted">Copy this now — it will not be shown again.</p>'
                  '<pre class="code-preview"><code>%s</code></pre>'
                  '<div class="row" style="margin-top:10px">'
                  '<a class="btn" href="/settings">%s Back to settings</a>'
                  '</div></div>' % (icon("shield", "ico ico-lg"),
                                    html_escape(new_token), icon("back")))
    return html_response(body)


@web_app.route("/settings/clear-token", methods=['POST'])
def settings_clear_token(request):
    gate = _setup_gate(request, "/settings/clear-token")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/settings", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    try:
        clear_api_token()
    except Exception as ex:
        print("token clear failed:", ex)
        return redirect("/settings", flash="bad_request")
    audit("api.token_cleared")
    return redirect("/settings", flash="token_cleared")


@web_app.route("/settings/factory-reset", methods=['POST'])
def settings_factory_reset(request):
    gate = _setup_gate(request, "/settings/factory-reset")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/settings", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    if (form.get('confirm') or '').strip() != "RESET":
        return redirect("/settings", flash="bad_request")
    try:
        factory_reset()
    except Exception as ex:
        print("factory reset failed:", ex)
        return redirect("/settings", flash="bad_request")
    audit("system.factory_reset")
    # All creds gone — redirect to setup
    body = layout("Factory reset",
                  '<div class="card"><h1>%s Factory reset complete</h1>'
                  '<p class="muted">Every payload, backup, audit entry, pin '
                  'and credential has been removed.</p>'
                  '<p><a class="btn primary" href="/setup">%s Start setup</a></p>'
                  '</div>' % (icon("ok", "ico ico-lg"), icon("lock")))
    return html_response(body)


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
        form = parse_form_single(request_body(request))
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
        '<div class="card"><h1>%s Rebooting...</h1>'
        '<p class="muted">The device will be back online in a moment.</p>'
        '</div>' % icon("reboot", "ico ico-lg"), active="system"))


# ---- Wipe ----
@web_app.route("/wipe", methods=['POST'])
def wipe_route(request):
    gate = _setup_gate(request, "/wipe")
    if gate:
        return gate
    auth = _auth_gate(request)
    if auth:
        return auth
    try:
        form = parse_form_single(request_body(request))
    except ValueError:
        return redirect("/", flash="bad_request")
    csrf = _csrf_gate(form)
    if csrf:
        return csrf
    removed = 0
    try:
        for name, _, _ in list_payloads():
            sf = safe_filename(name)
            if sf:
                try:
                    delete_file_safely(sf)
                    removed += 1
                except Exception:
                    pass
        # Clear pins too
        write_pins(set())
    except Exception as ex:
        print("wipe failed:", ex)
        return redirect("/", flash="bad_request")
    audit("payload.wipe", "%d removed" % removed)
    return redirect("/", flash="wiped")


# ---- Logout ----
@web_app.route("/logout")
def logout_route(request):
    body = ('<div class="card"><h1>%s Signed out</h1>'
            '<p class="muted">You have been signed out. '
            '<a href="/">Sign in again</a>.</p></div>') % icon("ok", "ico ico-lg")
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky - signed out"')],
            layout("Signed out", body))


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
            form = parse_form_single(request_body(request))
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


# ---- Health ----
@web_app.route("/health")
def health(request):
    return text_response("ok\n")


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
    body = '\n'.join("%s\t%d" % (n, s) for n, s, _ in list_payloads()) + '\n'
    return text_response(body)


@web_app.route("/api/system")
def api_system(request):
    if not api_authenticated(request):
        return text_response("unauthorized", status="401 Unauthorized")
    return json_response(_system_facts())


# ===========================================================================
# Server entry point
# ===========================================================================
async def startWebService():
    HOST = repr(wifi.radio.ipv4_address_ap)
    PORT = 80
    print(HOST, PORT)
    wsgiServer = server.WSGIServer(PORT, application=web_app)
    print("open this IP in your browser: http://%s:%d/" % (HOST, PORT))
    if SETUP_REQUIRED:
        print("first-run setup required: browse to /setup")
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
