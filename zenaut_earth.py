"""ZENAUT · Connection with Earth > Messages from home (Flask blueprint at /connection).

Loved ones send messages, notes, photos and videos. Everything that has arrived is stored on this device
in ./zenaut_earth_data/, so it can be read and watched with NO internet. New items arrive when there is a link.

How items get from Earth to the astronaut
  1. Family portal  (/connection/portal): a simple page where a loved one types the family code, then sends a
     message, note, photo or video. Run ZENAUT on a ground computer or server (python app.py, host 0.0.0.0) and
     give your family its address and the family code shown on the Messages from home page.
  2. Ground relay: on the astronaut's computer, enter the ground computer's address and its family code under
     "Connection settings". Whenever the astronaut is online, ZENAUT downloads anything new (when this page
     is open, and every 5 minutes in the background). Downloaded items stay available offline.
Sending from the same computer works too (the portal also saves straight into this device's inbox).
The family code is a simple shared password, not strong security. Use a trusted network or add HTTPS before
real use. Not a medical device.
"""
import hmac
import json
import os
import re
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

earthlink = Blueprint("earthlink", __name__, url_prefix="/connection")

BASE = Path(__file__).with_name("zenaut_earth_data")
FILES = BASE / "files"
DB_FILE = BASE / "inbox.json"
LOCK = threading.RLock()

IMAGE_EXT = {"jpg", "jpeg", "png", "gif", "webp"}
VIDEO_EXT = {"mp4", "webm", "mov", "m4v", "ogv"}
MIME = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp",
        "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime", "m4v": "video/mp4", "ogv": "video/ogg"}
KINDS = [("message", "Message"), ("note", "Note"), ("photo", "Photo"), ("video", "Video")]
KIND_NAME = dict(KINDS)
LIMIT = {"photo": 30 * 1024 * 1024, "video": 500 * 1024 * 1024}
CODE_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

CSS = """
.wb .item{padding:1rem 0;border-bottom:1px solid rgba(174,178,177,.2)}.wb .item:last-child{border:0}
.wb .item .head{display:flex;gap:.5rem;align-items:center;flex-wrap:wrap}
.wb .item .head b{font-size:1rem}
.wb .item .txt{white-space:pre-wrap;margin:.4rem 0}
.wb .item img.media,.wb .item video.media{max-width:100%;max-height:420px;border-radius:12px;display:block;margin:.5rem 0;background:#000}
.wb .item form{display:inline}
.wb .btn.sm{padding:.25rem .8rem;font-size:.75rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
.wb textarea{width:100%;min-height:110px;margin-top:.2rem;padding:.6rem;border-radius:10px;background:var(--space);color:var(--text);border:1px solid var(--grey);font:inherit;resize:vertical}
.wb input[type=file]{padding:.6rem}
.wb .code{font-size:1.8rem;font-weight:800;letter-spacing:.2em;font-family:Consolas,Menlo,monospace}
.wb .chips a{padding:.3rem .8rem;font-size:.75rem}
"""

# ------------------------------------------------------------------ storage
def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M")

def load():
    with LOCK:
        FILES.mkdir(parents=True, exist_ok=True)
        try:
            d = json.loads(DB_FILE.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            d = {}
        d.setdefault("items", [])
        d.setdefault("code", "")
        d.setdefault("relay_url", "")
        d.setdefault("relay_code", "")
        d.setdefault("last_sync", "")
        d.setdefault("last_error", "")
        d.setdefault("skip", [])
        if not d["code"]:
            d["code"] = "".join(secrets.choice(CODE_CHARS) for _ in range(8))
            save(d)
        return d

def save(d):
    with LOCK:
        FILES.mkdir(parents=True, exist_ok=True)
        tmp = DB_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, indent=2), encoding="utf-8")
        tmp.replace(DB_FILE)

def ext_of(name):
    return Path(name or "").suffix.lower().lstrip(".")

def check_file(kind, name):
    """Return None if the file name suits the kind, otherwise a friendly error."""
    ext = ext_of(name)
    ok = IMAGE_EXT if kind == "photo" else VIDEO_EXT
    if ext not in ok:
        return f'"{name}" is not a supported {kind} file. Use: ' + ", ".join(sorted(ok)) + "."
    return None

def new_item(kind, from_name, relation, text, title="", ext="", sent=None, remote_id="", origin="portal"):
    return dict(id=uuid.uuid4().hex[:10], kind=kind, from_name=(from_name or "Someone at home")[:60], relation=(relation or "")[:40],
                title=(title or "")[:80], text=(text or "")[:10000], ext=ext, sent=sent or now_s(), received=now_s(),
                seen=False, remote_id=remote_id, origin=origin)

def store_item(item, data=None):
    """Save an item (and its file bytes, if any) under the lock."""
    with LOCK:
        d = load()
        if data is not None and item["ext"]:
            (FILES / f'{item["id"]}.{item["ext"]}').write_bytes(data)
        d["items"].append(item)
        d["items"] = d["items"][-1000:]
        save(d)

def file_path(item):
    return FILES / f'{item["id"]}.{item["ext"]}' if item.get("ext") else None

def unseen_count():
    try:
        return sum(1 for i in load()["items"] if not i["seen"])
    except Exception:
        return 0

# ------------------------------------------------------------------ pulling from a ground relay
def pull_from_relay():
    """Download anything new from the ground relay. Returns dict(ok, new, error). Never raises."""
    d = load()
    base = d["relay_url"].strip().rstrip("/")
    if not base:
        return dict(ok=False, new=0, error="No ground relay set")
    code = d["relay_code"]
    new, error = 0, ""
    try:
        q = urllib.parse.urlencode({"code": code})
        with urllib.request.urlopen(f"{base}/connection/relay/items?{q}", timeout=10) as r:
            payload = json.loads(r.read().decode("utf-8"))
        known = {i.get("remote_id") for i in d["items"]} | set(d["skip"])
        for it in payload.get("items", []):
            rid, kind, ext = str(it.get("id", "")), it.get("kind"), (it.get("ext") or "").lower()
            if not rid or rid in known or kind not in KIND_NAME:
                continue
            if kind in ("photo", "video") and ext not in (IMAGE_EXT if kind == "photo" else VIDEO_EXT):
                continue
            item = new_item(kind, it.get("from_name"), it.get("relation"), it.get("text"), it.get("title"),
                            ext if kind in ("photo", "video") else "", it.get("sent"), rid, "relay")
            if item["ext"]:
                tmp = FILES / f'{item["id"]}.part'
                with urllib.request.urlopen(f"{base}/connection/relay/file/{urllib.parse.quote(rid)}?{q}", timeout=30) as r, open(tmp, "wb") as out:
                    while chunk := r.read(1 << 20):
                        out.write(chunk)
                tmp.replace(FILES / f'{item["id"]}.{item["ext"]}')
            store_item(item)
            new += 1
    except urllib.error.HTTPError as e:
        error = "the relay code is not right" if e.code == 403 else f"the relay answered with error {e.code}"
    except Exception as e:  # offline, wrong address, timeout, bad JSON
        error = (str(getattr(e, "reason", e)) or "no connection")[:120]
    with LOCK:
        d = load()
        if error:
            d["last_error"] = error
        else:
            d["last_sync"], d["last_error"] = now_s(), ""
        save(d)
    return dict(ok=not error, new=new, error=error)

def start_background_sync(interval=300):
    """Try the ground relay every few minutes, so new items arrive even when this page is closed."""
    def loop():
        while True:
            time.sleep(interval)
            try:
                if load()["relay_url"]:
                    pull_from_relay()
            except Exception:
                pass
    threading.Thread(target=loop, daemon=True, name="zenaut-earth-sync").start()

# ------------------------------------------------------------------ page shell
def tabs(active="home"):
    items = [("home", "Messages from home", url_for("earthlink.home")), ("mission", "Mission & emergency", url_for("astro.earth"))]
    return "".join(f'<a class="{"on" if k == active else ""}" href="{u}">{t}</a>' for k, t, u in items)

def page(body, links=""):
    return render_template("wellbeing.html", title="Messages, photos, videos and notes from your loved ones", heading="Connection with Earth",
                           links=Markup(links), body=Markup(f"<style>{CSS}</style>{body}"), css=Markup(WB_CSS), active="earth")

def item_html(it):
    who = escape(it["from_name"]) + (f' <span class="small">({escape(it["relation"])})</span>' if it.get("relation") else "")
    new = '<span class="badge orange">New</span>' if not it["seen"] else ""
    head = (f'<div class="head"><b>{who}</b> <span class="badge green" style="background:var(--panel2);color:var(--text)">{KIND_NAME[it["kind"]]}</span> {new}'
            f'<span class="small">sent {escape(it["sent"])}</span></div>')
    body = f'<div><b>{escape(it["title"])}</b></div>' if it.get("title") else ""
    if it.get("text"):
        body += f'<div class="txt">{escape(it["text"])}</div>'
    src = url_for("earthlink.media", iid=it["id"])
    if it["kind"] == "photo" and file_path(it) and file_path(it).exists():
        body += f'<a href="{src}" target="_blank" rel="noopener"><img class="media" src="{src}" alt="Photo from {escape(it["from_name"])}" loading="lazy"></a>'
    elif it["kind"] == "video" and file_path(it) and file_path(it).exists():
        body += f'<video class="media" src="{src}" controls preload="metadata"></video>'
    elif it["kind"] in ("photo", "video"):
        body += '<p class="small">The file is missing from this device.</p>'
    acts = ""
    if not it["seen"]:
        acts += (f'<form method="post" action="{url_for("earthlink.mark_read", iid=it["id"])}"><button class="btn sm">Mark as read</button></form> ')
    acts += (f'<form method="post" action="{url_for("earthlink.delete", iid=it["id"])}" onsubmit="return confirm(\'Delete this item from this device?\')">'
             '<button class="btn sm">Delete</button></form>')
    return f'<div class="item">{head}{body}<div style="margin-top:.4rem">{acts}</div></div>'

SYNC_JS = """<script>
(function(){
  var st=document.getElementById("cxstate"),btn=document.getElementById("cxsync"),configured=%s,busy=false;
  function offline(){st.innerHTML="<b>Offline.</b> You can still read everything saved on this device. New items will arrive when you are online.";}
  function sync(){
    if(busy)return;
    if(!navigator.onLine){offline();return;}
    if(!configured){st.innerHTML="<b>Online.</b> No ground relay is set yet. Add one under Connection settings to receive new items.";return;}
    busy=true;st.textContent="Online. Checking for new items...";
    fetch("%s",{method:"POST"}).then(function(r){return r.json()}).then(function(j){
      busy=false;
      if(j.ok){st.innerHTML="<b>Online and up to date.</b> "+(j.new?j.new+" new item(s) received.":"Nothing new right now.");if(j.new)setTimeout(function(){location.reload()},800);}
      else{st.innerHTML="<b>Online, but the ground relay could not be reached</b> ("+j.error.replace(/</g,"&lt;")+"). Showing what is saved on this device.";}
    }).catch(function(){busy=false;st.innerHTML="<b>Could not check right now.</b> Showing what is saved on this device.";});
  }
  btn.onclick=sync;
  window.addEventListener("online",sync);window.addEventListener("offline",offline);
  setInterval(sync,300000);sync();
})();
</script>"""

# ------------------------------------------------------------------ astronaut pages
@earthlink.route("/")
def home():
    d = load()
    show = request.args.get("show", "all")
    items = sorted(d["items"], key=lambda i: i["received"], reverse=True)
    n_new = sum(1 for i in items if not i["seen"])
    if show == "new":
        items = [i for i in items if not i["seen"]]
    elif show in KIND_NAME:
        items = [i for i in items if i["kind"] == show]
    chips = [("all", "All"), ("new", f"New ({n_new})")] + [(k, v + "s") for k, v in KINDS]
    chip_html = "".join(f'<a class="{"on" if k == show else ""}" href="{url_for("earthlink.home", show=k)}">{escape(t)}</a>' for k, t in chips)
    banner = (f'<div class="alert orange"><b>{n_new} new item{"" if n_new == 1 else "s"} from home.</b> '
              f'<form method="post" action="{url_for("earthlink.mark_all_read")}" style="display:inline"><button class="btn sm" style="margin-left:.5rem">Mark all as read</button></form></div>') if n_new else ""
    listing = "".join(item_html(i) for i in items) or '<p class="small">Nothing here yet. When your loved ones send something, it appears here and stays available offline.</p>'
    status = "No ground relay set yet." if not d["relay_url"] else (
        f'Last successful check: {escape(d["last_sync"])}.' if d["last_sync"] else "Not checked yet.")
    if d["last_error"] and d["relay_url"]:
        status += f' Last problem: {escape(d["last_error"])}.'
    portal_url = url_for("earthlink.portal", _external=True)
    body = (f'<div class="card"><h2>Connection</h2><div id="cxstate">Checking...</div>'
            f'<p class="small" style="margin-top:.4rem">{status} {len(d["items"])} item(s) saved on this device, available offline.</p>'
            '<div class="row"><button class="btn" id="cxsync" type="button">Check for new items</button>'
            f'<a class="btn" href="{url_for("astro.earth")}">Reply to loved ones</a></div></div>'
            + banner +
            f'<div class="card"><h2>From your loved ones</h2><nav class="chips" style="margin:0 0 .6rem">{chip_html}</nav>{listing}</div>'
            '<div class="card"><h2>Let your loved ones send you something</h2>'
            f'<p>Give your family the portal address and this family code. They can send messages, notes, photos and videos.</p>'
            f'<p style="margin:.5rem 0"><span class="small">Family code</span><br><span class="code">{escape(d["code"])}</span></p>'
            f'<p><span class="small">Portal address</span><br><a href="{url_for("earthlink.portal")}" style="text-decoration:underline" target="_blank" rel="noopener">{escape(portal_url)}</a></p>'
            '<p class="small" style="margin-top:.6rem">The portal must run on a computer your family can reach (a ground computer or server). '
            'Then set that address below so this device can download new items whenever you are online.</p></div>'
            '<div class="card"><h2>Connection settings (ground relay)</h2>'
            f'<form method="post" action="{url_for("earthlink.settings")}" class="log">'
            f'<div style="grid-column:1/-1"><label for="rurl">Ground relay address</label><input id="rurl" name="relay_url" value="{escape(d["relay_url"])}" placeholder="http://192.168.1.20:5000 or https://ground.example.org"></div>'
            f'<div><label for="rcode">Relay family code</label><input id="rcode" name="relay_code" value="{escape(d["relay_code"])}" maxlength="20" autocomplete="off"></div>'
            '<div style="align-self:end"><button class="btn primary">Save settings</button></div></form></div>'
            + SYNC_JS % (json.dumps(bool(d["relay_url"])), url_for("earthlink.sync")))
    return page(body, tabs("home"))

@earthlink.route("/media/<iid>")
def media(iid):
    it = next((i for i in load()["items"] if i["id"] == iid), None)
    p = file_path(it) if it else None
    if not p or not p.exists() or it["ext"] not in MIME:
        abort(404)
    return send_file(p, mimetype=MIME[it["ext"]], as_attachment=False, conditional=True)

@earthlink.route("/read/<iid>", methods=["POST"])
def mark_read(iid):
    with LOCK:
        d = load()
        for i in d["items"]:
            if i["id"] == iid:
                i["seen"] = True
        save(d)
    return redirect(request.referrer or url_for("earthlink.home"))

@earthlink.route("/read-all", methods=["POST"])
def mark_all_read():
    with LOCK:
        d = load()
        for i in d["items"]:
            i["seen"] = True
        save(d)
    return redirect(url_for("earthlink.home"))

@earthlink.route("/delete/<iid>", methods=["POST"])
def delete(iid):
    with LOCK:
        d = load()
        it = next((i for i in d["items"] if i["id"] == iid), None)
        if it:
            p = file_path(it)
            if p:
                p.unlink(missing_ok=True)
            if it.get("remote_id"):
                d["skip"] = (d["skip"] + [it["remote_id"]])[-2000:]  # do not download it again
            d["items"] = [i for i in d["items"] if i["id"] != iid]
            save(d)
    return redirect(url_for("earthlink.home"))

@earthlink.route("/settings", methods=["POST"])
def settings():
    url = request.form.get("relay_url", "").strip()
    if url and not re.match(r"^https?://", url, re.I):
        url = "http://" + url
    with LOCK:
        d = load()
        d["relay_url"] = url[:300]
        d["relay_code"] = request.form.get("relay_code", "").strip().upper()[:20]
        d["last_error"] = ""
        save(d)
    return redirect(url_for("earthlink.home"))

@earthlink.route("/sync", methods=["POST"])
def sync():
    return jsonify(pull_from_relay())

# ------------------------------------------------------------------ family portal (what loved ones see)
PORTAL = """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Send a message home to space</title><style>%s
body{margin:0;background:#071E4A;font:16px/1.55 "Helvetica Neue",Helvetica,Arial,sans-serif}
.wb{margin:0 auto;padding:22px 16px 40px}.wb h1{margin-top:0}
</style></head><body><section class="wb"><h1>Send something to your astronaut</h1>
<p class="small">Type the family code you were given, then write a message or share a photo or video. It will reach them as soon as they have a connection.</p>
%s
<form method="post" enctype="multipart/form-data" class="card">
<div class="grid"><div><label for="code">Family code</label><input id="code" name="code" required autocomplete="off" maxlength="20" style="text-transform:uppercase"></div>
<div><label for="from_name">Your name</label><input id="from_name" name="from_name" required maxlength="60" value="%s"></div>
<div><label for="relation">Who are you to them? (optional)</label><input id="relation" name="relation" maxlength="40" placeholder="Mum, sister, friend..." value="%s"></div>
<div><label for="kind">What are you sending?</label><select id="kind" name="kind">%s</select></div></div>
<div id="ttl" style="margin-top:.8rem"><label for="title">Title (for a note)</label><input id="title" name="title" maxlength="80"></div>
<label for="text" style="margin-top:.8rem">Your message or caption</label><textarea id="text" name="text" maxlength="10000"></textarea>
<div id="fil" style="margin-top:.8rem"><label for="files">Photo or video file (you can choose several photos)</label><input id="files" type="file" name="files" multiple accept="image/*,video/*"></div>
<div class="row"><button class="btn primary">Send</button></div></form>
<p class="small" style="text-align:center;margin-top:1.5rem">Photos up to 30 MB each. Videos up to 500 MB (mp4, webm, mov, m4v, ogv).</p></section>
<script>(function(){var k=document.getElementById("kind"),t=document.getElementById("ttl"),f=document.getElementById("fil");
function u(){t.style.display=k.value==="note"?"":"none";f.style.display=(k.value==="photo"||k.value==="video")?"":"none";}
k.onchange=u;u();})();</script></body></html>"""

@earthlink.route("/portal", methods=["GET", "POST"])
def portal():
    d = load()
    note, name, rel, kind_sel = "", "", "", "message"
    if request.method == "POST":
        f = request.form
        name, rel, kind_sel = f.get("from_name", "").strip(), f.get("relation", "").strip(), f.get("kind", "message")
        text, title = f.get("text", "").strip(), f.get("title", "").strip()
        files = [x for x in request.files.getlist("files") if x and x.filename]
        err = None
        if not hmac.compare_digest(f.get("code", "").strip().upper().encode(), d["code"].encode()):
            err = "That family code is not right. Please check it and try again."
        elif kind_sel not in KIND_NAME:
            err = "Please choose what you are sending."
        elif not name:
            err = "Please tell us your name."
        elif kind_sel in ("message", "note") and not text:
            err = "Please write something to send."
        elif kind_sel in ("photo", "video") and not files:
            err = f"Please choose a {kind_sel} file."
        elif kind_sel == "photo" or kind_sel == "video":
            err = next((check_file(kind_sel, x.filename) for x in files if check_file(kind_sel, x.filename)), None)
        if err:
            note = f'<div class="alert red">{escape(err)}</div>'
        else:
            sent = 0
            if kind_sel in ("message", "note"):
                store_item(new_item(kind_sel, name, rel, text, title if kind_sel == "note" else ""))
                sent = 1
            else:
                for n, x in enumerate(files):
                    data = x.read()
                    if not data or len(data) > LIMIT[kind_sel]:
                        note += f'<div class="alert orange">{escape(x.filename)} was skipped: it is empty or too large.</div>'
                        continue
                    store_item(new_item(kind_sel, name, rel, text if n == 0 else "", "", ext_of(x.filename)), data)
                    sent += 1
            if sent:
                note = f'<div class="alert green"><b>Sent.</b> {sent} item(s) will reach them as soon as they are connected.</div>' + note
    opts = "".join(f'<option value="{k}"{" selected" if k == kind_sel else ""}>{v}</option>' for k, v in KINDS)
    return PORTAL % (WB_CSS, note, escape(name, quote=True), escape(rel, quote=True), opts)

# ------------------------------------------------------------------ relay API (the astronaut's device calls these)
def relay_ok():
    code = (request.args.get("code") or request.headers.get("X-Family-Code") or "").strip().upper()
    return hmac.compare_digest(code.encode(), load()["code"].encode())

@earthlink.route("/relay/items")
def relay_items():
    if not relay_ok():
        return jsonify(error="wrong code"), 403
    keys = ("id", "kind", "from_name", "relation", "title", "text", "ext", "sent")
    return jsonify(items=[{k: i[k] for k in keys} for i in load()["items"] if i.get("origin") == "portal"][-500:])

@earthlink.route("/relay/file/<iid>")
def relay_file(iid):
    if not relay_ok():
        return jsonify(error="wrong code"), 403
    it = next((i for i in load()["items"] if i["id"] == iid and i.get("origin") == "portal"), None)
    p = file_path(it) if it else None
    if not p or not p.exists() or it["ext"] not in MIME:
        abort(404)
    return send_file(p, mimetype=MIME[it["ext"]], conditional=True)
