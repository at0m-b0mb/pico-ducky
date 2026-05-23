# License : GPLv2.0
# Original copyright (c) 2023  Dave Bailey (dbisu, @daveisu)
# Enhanced web interface: modern UI, optional auth, hardened input handling,
# additional features (upload, rename, download, snippets, system info).

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
# Optional HTTP Basic auth. Create a creds.py with:
#     WEB_USERNAME = "admin"
#     WEB_PASSWORD = "changeme"
# Leaving the file absent disables auth (legacy behaviour).
# ---------------------------------------------------------------------------
try:
    from creds import WEB_USERNAME, WEB_PASSWORD
except ImportError:
    WEB_USERNAME = ""
    WEB_PASSWORD = ""

AUTH_ENABLED = bool(WEB_USERNAME) and bool(WEB_PASSWORD)

# ---------------------------------------------------------------------------
# Config / limits
# ---------------------------------------------------------------------------
MAX_PAYLOAD_BYTES = 64 * 1024          # hard upper bound per script
MAX_FILENAME_LEN = 48
PAYLOAD_EXT = ".dd"
ALLOWED_NAME_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-."
)

BOOT_TIME = time.monotonic()

# ---------------------------------------------------------------------------
# CSS / layout
# ---------------------------------------------------------------------------
BASE_CSS = """
:root{
 --bg:#0b1220;--panel:#111a2e;--panel-2:#172241;--border:#22304d;
 --muted:#7c8bab;--text:#e6edf7;--accent:#22d3ee;--accent-2:#7c3aed;
 --danger:#f43f5e;--ok:#10b981;--warn:#f59e0b;--radius:10px;
 --shadow:0 6px 16px rgba(0,0,0,.25);
}
*{box-sizing:border-box}
html,body{margin:0;padding:0;background:var(--bg);color:var(--text);
 font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Inter,sans-serif;
 font-size:15px;line-height:1.5;min-height:100vh}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
.wrap{max-width:980px;margin:0 auto;padding:24px 16px}
header.app{display:flex;align-items:center;justify-content:space-between;
 padding:14px 20px;background:linear-gradient(135deg,#0e1a36,#11254f);
 border-bottom:1px solid var(--border);position:sticky;top:0;z-index:10;
 backdrop-filter:blur(8px)}
header.app .brand{display:flex;align-items:center;gap:10px;font-weight:700}
header.app .brand .logo{width:30px;height:30px;border-radius:8px;
 background:linear-gradient(135deg,var(--accent),var(--accent-2));
 display:inline-flex;align-items:center;justify-content:center;
 color:#06121f;font-weight:800;font-size:16px}
nav.app{display:flex;gap:6px;flex-wrap:wrap}
nav.app a{padding:6px 12px;border-radius:999px;font-size:14px;color:var(--muted)}
nav.app a.active,nav.app a:hover{background:rgba(34,211,238,.12);
 color:var(--text);text-decoration:none}
h1,h2,h3{margin:0 0 .5em 0}
h1{font-size:24px}
h2{font-size:18px;color:var(--text);font-weight:600}
h3{font-size:15px;color:var(--muted);font-weight:500;text-transform:uppercase;
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
.btn:hover{background:#1f2c52;border-color:#2c3e6a;text-decoration:none}
.btn:active{transform:translateY(1px)}
.btn.primary{background:linear-gradient(135deg,var(--accent),#0891b2);
 color:#06121f;border-color:transparent;font-weight:600}
.btn.primary:hover{filter:brightness(1.05)}
.btn.danger{background:transparent;color:var(--danger);border-color:rgba(244,63,94,.4)}
.btn.danger:hover{background:rgba(244,63,94,.12)}
.btn.warn{background:transparent;color:var(--warn);border-color:rgba(245,158,11,.4)}
.btn.warn:hover{background:rgba(245,158,11,.12)}
.btn.ghost{background:transparent}
.btn.small{padding:4px 10px;font-size:13px}
.btn[disabled]{opacity:.5;cursor:not-allowed}
.input,textarea,select{
 width:100%;padding:10px 12px;background:#0a1226;color:var(--text);
 border:1px solid var(--border);border-radius:8px;font-size:14px;
 font-family:inherit;outline:none;transition:border-color .15s}
.input:focus,textarea:focus{border-color:var(--accent)}
textarea{resize:vertical;min-height:220px;line-height:1.55;tab-size:4;
 font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace}
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
.alert.ok{background:rgba(16,185,129,.1);border-color:rgba(16,185,129,.35);color:#a7f3d0}
.alert.err{background:rgba(244,63,94,.1);border-color:rgba(244,63,94,.35);color:#fecdd3}
.alert.info{background:rgba(34,211,238,.08);border-color:rgba(34,211,238,.3);color:#bdf3fb}
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
.kbd{font-family:ui-monospace,monospace;background:#0a1226;padding:1px 6px;
 border:1px solid var(--border);border-radius:4px;font-size:12px}
.empty{padding:40px 20px;text-align:center;color:var(--muted)}
@media(max-width:600px){
 header.app{flex-direction:column;gap:10px;align-items:flex-start}
 .wrap{padding:16px 10px}
 th.hide-sm,td.hide-sm{display:none}
}
"""

NAV_ITEMS = (
    ("home", "/", "Payloads"),
    ("new", "/new", "New"),
    ("upload", "/upload", "Upload"),
    ("snippets", "/snippets", "Snippets"),
    ("system", "/system", "System"),
)


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


def layout(title, body, active="home", flash=None):
    nav_html = ""
    for k, href, label in NAV_ITEMS:
        cls = ' class="active"' if k == active else ""
        nav_html += '<a href="%s"%s>%s</a>' % (href, cls, label)

    flash_html = ""
    if flash:
        kind, msg = flash
        flash_html = '<div class="alert %s">%s</div>' % (
            kind, html_escape(msg))

    return (
        '<!DOCTYPE html><html lang="en"><head>'
        '<meta charset="utf-8"><title>%s &middot; Pico Ducky</title>'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        '<style>%s</style></head><body>'
        '<header class="app"><div class="brand">'
        '<span class="logo">D</span>Pico Ducky</div>'
        '<nav class="app">%s</nav></header>'
        '<div class="wrap">%s%s</div>'
        '<footer class="app">pico-ducky &middot; running on the Pico W AP'
        '</footer></body></html>'
    ) % (html_escape(title), BASE_CSS, nav_html, flash_html, body)


# ---------------------------------------------------------------------------
# Request helpers
# ---------------------------------------------------------------------------
_hexdig = '0123456789ABCDEFabcdef'


def cleanup_text(s):
    """URL-decode a form value to a str. Tolerates bytes or str input."""
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
    """Parse application/x-www-form-urlencoded body. Robust to '=' in values."""
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


# ---------------------------------------------------------------------------
# Auth
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


def is_authenticated(request):
    if not AUTH_ENABLED:
        return True
    auth = get_header(request, 'Authorization')
    if not auth or not auth.startswith('Basic '):
        return False
    decoded = _b64decode(auth[6:].strip())
    if ':' not in decoded:
        return False
    u, p = decoded.split(':', 1)
    return _ct_equals(u, WEB_USERNAME) and _ct_equals(p, WEB_PASSWORD)


def unauthorized_response():
    body = layout("Sign in", '<div class="card"><h1>Sign in required</h1>'
                  '<p class="muted">Enter the credentials you set in '
                  '<span class="kbd">creds.py</span>.</p></div>')
    return ("401 Unauthorized",
            [("Content-Type", "text/html; charset=utf-8"),
             ("WWW-Authenticate", 'Basic realm="Pico Ducky"')],
            body)


# ---------------------------------------------------------------------------
# Response helpers
# ---------------------------------------------------------------------------
SEC_HEADERS = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
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


# ---------------------------------------------------------------------------
# Filename validation (prevents path traversal)
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


def file_exists(name):
    try:
        os.stat(name)
        return True
    except OSError:
        return False


def file_size(name):
    try:
        st = os.stat(name)
        return st[6]
    except Exception:
        return 0


def format_size(n):
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


def write_file_safely(name, contents):
    """Atomically remount, write, remount-readonly. Always restores RO."""
    if len(contents) > MAX_PAYLOAD_BYTES:
        raise ValueError("payload exceeds %d bytes" % MAX_PAYLOAD_BYTES)
    try:
        storage.remount("/", readonly=False)
        with open(name, "w") as f:
            f.write(contents)
    finally:
        try:
            storage.remount("/", readonly=True)
        except Exception:
            pass


def delete_file_safely(name):
    try:
        storage.remount("/", readonly=False)
        os.remove(name)
    finally:
        try:
            storage.remount("/", readonly=True)
        except Exception:
            pass


def rename_file_safely(old, new):
    try:
        storage.remount("/", readonly=False)
        os.rename(old, new)
    finally:
        try:
            storage.remount("/", readonly=True)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Flash message extraction (very tiny, no session store)
# ---------------------------------------------------------------------------
FLASH_MESSAGES = {
    "saved": ("ok", "Payload saved."),
    "deleted": ("ok", "Payload deleted."),
    "renamed": ("ok", "Payload renamed."),
    "uploaded": ("ok", "Payload uploaded."),
    "ran": ("ok", "Payload executed."),
    "bad_name": ("err", "Invalid filename. Use only A-Z, 0-9, _ , - and end with .dd"),
    "too_big": ("err", "Payload too large."),
    "not_found": ("err", "Payload not found."),
    "exists": ("err", "A payload with that name already exists."),
    "bad_request": ("err", "Bad request."),
}


def get_flash(request):
    qs = ''
    env = getattr(request, 'environ', None)
    if env is not None:
        try:
            qs = env.get('QUERY_STRING', '') or ''
        except AttributeError:
            qs = getattr(env, 'QUERY_STRING', '') or ''
    if not qs:
        qp = getattr(request, 'query_params', None)
        if isinstance(qp, dict):
            key = qp.get('m')
            if key:
                return FLASH_MESSAGES.get(key)
        elif isinstance(qp, str):
            qs = qp
    if not qs:
        path = getattr(request, 'path', '') or ''
        if '?' in path:
            qs = path.split('?', 1)[1]
    if not qs:
        return None
    for pair in qs.split('&'):
        if pair.startswith('m='):
            return FLASH_MESSAGES.get(pair[2:])
    return None


# ---------------------------------------------------------------------------
# Snippet library (DuckyScript essentials)
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
            '<a class="btn small ghost" href="/download/%s">Download</a>'
            '<form method="post" action="/run/%s" style="display:inline">'
            '<button class="btn small warn" type="submit" '
            'onclick="return confirm(\'Run %s? This will inject keystrokes.\')">'
            'Run</button></form>'
            '<form method="post" action="/delete/%s" style="display:inline">'
            '<button class="btn small danger" type="submit" '
            'onclick="return confirm(\'Delete %s permanently?\')">'
            'Delete</button></form>'
            '</div></td>'
            '</tr>'
            % (safe, format_size(size), safe, safe, safe, safe, safe, safe)
        )

    body = (
        '<div class="card">'
        '<div class="row"><h1>Payloads</h1>'
        '<span class="badge">%d</span><div class="spacer"></div>'
        '<a class="btn primary" href="/new">+ New Script</a>'
        '<a class="btn" href="/upload">Upload</a>'
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
    ) % (len(payloads), ''.join(rows))
    return layout("Payloads", body, active="home", flash=flash)


def render_editor(filename, contents, flash=None):
    safe = html_escape(filename)
    snip_buttons = ''.join(
        '<button type="button" class="snip" data-snip="%s">%s</button>'
        % (html_escape(code).replace('\n', '\\n'), html_escape(label))
        for label, code in SNIPPETS
    )
    body = (
        '<div class="card"><div class="row">'
        '<h1>Editing %s</h1><div class="spacer"></div>'
        '<a class="btn" href="/">Cancel</a></div>'
        '<form method="post" action="/write/%s" id="ed">'
        '<div class="field">'
        '<label>Script</label>'
        '<textarea name="scriptData" id="ta">%s</textarea>'
        '<p class="muted" style="margin:6px 0 0 0;font-size:12px">'
        'Max %s. Tip: click a snippet below to insert.</p>'
        '</div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Save</button>'
        '<a class="btn" href="/download/%s">Download</a>'
        '<form method="post" action="/run/%s" style="display:inline">'
        '<button class="btn warn" type="submit" '
        'onclick="return confirm(\'Run now?\')">Run</button></form>'
        '</div></form></div>'
        '<div class="card"><h3>Snippets</h3>'
        '<div class="snippets">%s</div></div>'
        '<script>'
        'var ta=document.getElementById("ta");'
        'document.querySelectorAll(".snip").forEach(function(b){'
        'b.addEventListener("click",function(){'
        'var s=b.getAttribute("data-snip").replace(/\\\\n/g,"\\n");'
        'var p=ta.selectionStart||0;'
        'ta.value=ta.value.slice(0,p)+s+"\\n"+ta.value.slice(p);'
        'ta.focus();'
        '})});'
        '</script>'
    ) % (safe, safe, html_escape(contents),
         format_size(MAX_PAYLOAD_BYTES), safe, safe, snip_buttons)
    return layout("Edit " + filename, body, active="home", flash=flash)


def render_new(flash=None, prefill_name="", prefill_data=""):
    body = (
        '<div class="card"><h1>New Script</h1>'
        '<form method="post" action="/new">'
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
    ) % (html_escape(prefill_name), html_escape(prefill_data))
    return layout("New script", body, active="new", flash=flash)


def render_upload(flash=None):
    body = (
        '<div class="card"><h1>Upload Payload</h1>'
        '<p class="muted">Select a local <span class="kbd">.dd</span> file. '
        'It will be read in the browser and posted to the device.</p>'
        '<div class="field">'
        '<input type="file" id="file" accept=".dd,.txt,text/plain" class="input">'
        '</div>'
        '<div class="field"><label>Save as</label>'
        '<input class="input" id="name" placeholder="payload.dd"></div>'
        '<form method="post" action="/new" id="upf">'
        '<input type="hidden" name="scriptName" id="hname">'
        '<textarea name="scriptData" id="hdata" '
        'style="display:none"></textarea>'
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
    )
    return layout("Upload", body, active="upload", flash=flash)


def render_snippets(flash=None):
    items = ''
    for label, code in SNIPPETS:
        items += (
            '<div class="card"><div class="row">'
            '<h2>%s</h2><div class="spacer"></div>'
            '<button class="btn small" onclick="copyText(this)">Copy</button>'
            '</div>'
            '<pre style="background:#0a1226;padding:12px;border-radius:8px;'
            'overflow:auto;border:1px solid var(--border);'
            'font-family:ui-monospace,monospace;font-size:13px;margin:8px 0 0">'
            '<code>%s</code></pre></div>'
        ) % (html_escape(label), html_escape(code))
    body = (
        '<div class="card"><h1>DuckyScript Snippets</h1>'
        '<p class="muted">Common building blocks. Click Copy or use them '
        'from inside the editor.</p></div>%s'
        '<script>'
        'function copyText(b){var c=b.parentElement.parentElement'
        '.querySelector("code").innerText;'
        'if(navigator.clipboard){navigator.clipboard.writeText(c);'
        'b.innerText="Copied"}else{b.innerText="Select manually"}}'
        '</script>'
    ) % items
    return layout("Snippets", body, active="snippets", flash=flash)


def render_system(flash=None):
    gc.collect()
    free = gc.mem_free() if hasattr(gc, 'mem_free') else 0
    alloc = gc.mem_alloc() if hasattr(gc, 'mem_alloc') else 0
    board_id = getattr(board, 'board_id', '?')
    try:
        ap_ip = repr(wifi.radio.ipv4_address_ap)
    except Exception:
        ap_ip = "?"
    try:
        ap_ssid = wifi.radio.ap_info.ssid if wifi.radio.ap_info else "?"
    except Exception:
        ap_ssid = "?"
    cpu_temp = "?"
    if microcontroller and hasattr(microcontroller, 'cpu'):
        try:
            cpu_temp = "%.1f \xb0C" % microcontroller.cpu.temperature
        except Exception:
            pass
    uptime = format_uptime(time.monotonic() - BOOT_TIME)
    payload_count = len(list_payloads())
    try:
        ro = storage.getmount('/').readonly
        fs = "read-only" if ro else "writable"
    except Exception:
        fs = "?"

    auth_state = "enabled" if AUTH_ENABLED else "disabled"

    stats = [
        ("Board", board_id),
        ("Access Point IP", ap_ip),
        ("Uptime", uptime),
        ("Payloads", str(payload_count)),
        ("Free memory", format_size(free) if free else "?"),
        ("Used memory", format_size(alloc) if alloc else "?"),
        ("CPU temperature", cpu_temp),
        ("Filesystem", fs),
        ("Web auth", auth_state),
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
        '<form method="post" action="/system/reboot" style="display:inline">'
        '<button class="btn danger" type="submit" '
        'onclick="return confirm(\'Reboot the Pico now?\')">Reboot</button>'
        '</form></div></div>'
    ) % cards
    return layout("System", body, active="system", flash=flash)


def render_rename(filename, flash=None):
    safe = html_escape(filename)
    body = (
        '<div class="card"><h1>Rename %s</h1>'
        '<form method="post" action="/rename/%s">'
        '<div class="field"><label>New name</label>'
        '<input class="input" name="newName" value="%s" required></div>'
        '<div class="row">'
        '<button class="btn primary" type="submit">Rename</button>'
        '<a class="btn" href="/">Cancel</a></div></form></div>'
    ) % (safe, safe, safe)
    return layout("Rename " + filename, body, active="home", flash=flash)


# ---------------------------------------------------------------------------
# WSGI routes
# ---------------------------------------------------------------------------
web_app = WSGIApp()


def _auth_or_401(request):
    if not is_authenticated(request):
        return unauthorized_response()
    return None


@web_app.route("/")
def index(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    return html_response(render_home(flash=get_flash(request)))


@web_app.route("/ducky")
def ducky_compat(request):
    return index(request)


@web_app.route("/new", methods=['GET', 'POST'])
def new_script(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    if request.method == 'GET':
        return html_response(render_new(flash=get_flash(request)))
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/new", flash="too_big")
    name = normalize_payload_name(form.get('scriptName', ''))
    if not name:
        return html_response(render_new(
            flash=("err", "Invalid filename. Use letters, digits, _ , - only."),
            prefill_name=form.get('scriptName', ''),
            prefill_data=form.get('scriptData', '')))
    data = form.get('scriptData', '')
    if len(data) > MAX_PAYLOAD_BYTES:
        return redirect("/new", flash="too_big")
    try:
        write_file_safely(name, data)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/new", flash="bad_request")
    return redirect("/", flash="saved")


@web_app.route("/edit/<filename>")
def edit(request, filename):
    auth = _auth_or_401(request)
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


@web_app.route("/write/<filename>", methods=['POST'])
def write_script(request, filename):
    auth = _auth_or_401(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name:
        return redirect("/", flash="bad_name")
    try:
        form = parse_form(request_body(request))
    except ValueError:
        return redirect("/", flash="too_big")
    data = form.get('scriptData', '')
    if len(data) > MAX_PAYLOAD_BYTES:
        return redirect("/edit/" + name, flash="too_big")
    try:
        write_file_safely(name, data)
    except Exception as ex:
        print("write failed:", ex)
        return redirect("/edit/" + name, flash="bad_request")
    return redirect("/edit/" + name, flash="saved")


@web_app.route("/delete/<filename>", methods=['GET', 'POST'])
def delete_route(request, filename):
    auth = _auth_or_401(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    try:
        delete_file_safely(name)
    except Exception as ex:
        print("delete failed:", ex)
        return redirect("/", flash="bad_request")
    return redirect("/", flash="deleted")


@web_app.route("/rename/<filename>", methods=['GET', 'POST'])
def rename_route(request, filename):
    auth = _auth_or_401(request)
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
    return redirect("/", flash="renamed")


@web_app.route("/download/<filename>")
def download(request, filename):
    auth = _auth_or_401(request)
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
    return text_response(contents, filename=name)


@web_app.route("/upload")
def upload_page(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    return html_response(render_upload(flash=get_flash(request)))


@web_app.route("/snippets")
def snippets_page(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    return html_response(render_snippets(flash=get_flash(request)))


@web_app.route("/system")
def system_page(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    return html_response(render_system(flash=get_flash(request)))


@web_app.route("/system/reboot", methods=['POST'])
def system_reboot(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    if microcontroller is None:
        return redirect("/system", flash="bad_request")
    # Fire-and-forget reboot
    async def _later():
        await asyncio.sleep(0.5)
        microcontroller.reset()
    asyncio.create_task(_later())
    return html_response(layout("Rebooting",
        '<div class="card"><h1>Rebooting...</h1>'
        '<p class="muted">The device will be back online in a moment.</p>'
        '</div>', active="system"))


@web_app.route("/run/<filename>", methods=['GET', 'POST'])
async def run_script_route(request, filename):
    auth = _auth_or_401(request)
    if auth:
        return auth
    name = safe_filename(filename)
    if not name or not file_exists(name):
        return redirect("/", flash="not_found")
    print("run_script", name)
    await runScript(name)
    return redirect("/", flash="ran")


def setPayload(payload_number):
    if payload_number == 1:
        return "payload.dd"
    return "payload" + str(payload_number) + ".dd"


@web_app.route("/api/run/<filenumber>")
async def api_run(request, filenumber):
    auth = _auth_or_401(request)
    if auth:
        return auth
    try:
        n = int(filenumber)
    except (TypeError, ValueError):
        return text_response("bad request", status="400 Bad Request")
    if n < 1 or n > 99:
        return text_response("bad request", status="400 Bad Request")
    name = setPayload(n)
    if not file_exists(name):
        return text_response("not found", status="404 Not Found")
    print("api_run", name)
    await runScript(name)
    return text_response("ok\n")


@web_app.route("/api/payloads")
def api_payloads(request):
    auth = _auth_or_401(request)
    if auth:
        return auth
    body = '\n'.join("%s\t%d" % (n, s) for n, s in list_payloads()) + '\n'
    return text_response(body)


# ---------------------------------------------------------------------------
# Server entry point
# ---------------------------------------------------------------------------
async def startWebService():
    HOST = repr(wifi.radio.ipv4_address_ap)
    PORT = 80
    print(HOST, PORT)
    wsgiServer = server.WSGIServer(PORT, application=web_app)
    print("open this IP in your browser: http://%s:%d/" % (HOST, PORT))
    if AUTH_ENABLED:
        print("web UI auth enabled (creds.py)")
    else:
        print("web UI auth DISABLED — set WEB_USERNAME/WEB_PASSWORD in creds.py")
    wsgiServer.start()
    while True:
        try:
            wsgiServer.update_poll()
        except Exception as ex:
            print("wsgi update error:", ex)
        await asyncio.sleep(0)
