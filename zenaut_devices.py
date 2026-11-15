"""ZENAUT · Physical health > Sync devices (adds routes to the physical blueprint).

Brings readings from hardware into the same places as typed-in readings, with the same safe-range checks,
red alerts and graphs. Manual entry on each tab is unchanged. Four ways in:
  1. Bluetooth (in the browser): heart-rate monitors, pulse oximeters, blood-pressure cuffs and glucose meters that
     use the standard Bluetooth health profiles. Works in Chrome or Edge (desktop or Android) on http://127.0.0.1.
     Page script: static/js/devices.js.
  2. File import: CSV or JSON exported by a device or its app.
  3. Device folder: drop CSV/JSON files into ./device_inbox/ (for example from a USB stick or a gateway program)
     and press "Sync from device folder".
  4. API for gateways and bridge programs: POST /physical/api/sync (JSON). Optionally protect it by setting the
     ZENAUT_DEVICE_TOKEN environment variable; callers then send it as the X-Device-Token header.
Repeated syncs never create duplicates (same time and same values are skipped). Every synced reading is tagged with
its source. Readings outside the typing limits (see each tab) are rejected and listed, not stored.
Not a medical device.
"""
import csv
import hmac
import io
import json
import math
import os
import re
import threading
import uuid
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from flask import Response, jsonify, redirect, request, url_for

from zenaut_physical import READINGS, fmt_value, load, page, physical, save, status_of, unit_of

INBOX = Path(__file__).with_name("device_inbox")
LOCK = threading.RLock()
MAX_RECORDS = 20000

def canon(s):
    return re.sub(r"[\s_]+", "-", str(s).strip().lower())

ALIASES = {
    "heart-rate": ["heart-rate", "heartrate", "hr", "pulse", "bpm"],
    "blood-oxygen": ["blood-oxygen", "spo2", "spo₂", "oxygen", "o2"],
    "blood-pressure": ["blood-pressure", "bp"],
    "blood-sugar": ["blood-sugar", "glucose", "sugar"],
    "hydration": ["hydration"],
    "radiation": ["radiation", "dose", "radiation-msv", "msv"],
    "bone-density": ["bone-density", "t-score", "tscore"],
}
LOOKUP = {canon(a): slug for slug, names in ALIASES.items() for a in names}
SYS_KEYS, DIA_KEYS = ("sys", "systolic"), ("dia", "diastolic")

# ------------------------------------------------------------------ turning records into readings
def first(rec, keys):
    return next((rec[k] for k in keys if rec.get(k) not in (None, "")), None)

def split_bp(v):
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*[/\\-]\s*(\d+(?:\.\d+)?)\s*$", str(v or ""))
    return (m.group(1), m.group(2)) if m else (None, None)

def normalize(rec):
    """One record (a CSV row or JSON object) -> list of (slug, raw values, time, note, error)."""
    if not isinstance(rec, dict):
        return [(None, {}, None, "", "Each reading must be an object with a type and a value")]
    rec = {canon(k): v for k, v in rec.items() if k is not None}
    t = first(rec, ("time", "timestamp", "date", "datetime"))
    note = str(rec.get("note") or "")
    typ = first(rec, ("type", "metric", "reading"))
    out = []
    if typ is not None:
        slug = LOOKUP.get(canon(typ))
        if not slug:
            return [(None, {}, t, note, f'Unknown reading type "{typ}"')]
        if slug == "blood-pressure":
            sy, di = first(rec, SYS_KEYS), first(rec, DIA_KEYS)
            if sy is None or di is None:
                sy, di = split_bp(rec.get("value"))
            out.append((slug, {"sys": sy, "dia": di}, t, note, None))
        else:
            out.append((slug, {"value": rec.get("value")}, t, note, None))
        return out
    for k, v in rec.items():  # wide format: one column per measurement
        slug = LOOKUP.get(k)
        if slug and v not in (None, ""):
            if slug == "blood-pressure":
                sy, di = split_bp(v)
                out.append((slug, {"sys": sy, "dia": di}, t, note, None))
            else:
                out.append((slug, {"value": v}, t, note, None))
    sy, di = first(rec, SYS_KEYS), first(rec, DIA_KEYS)
    if sy is not None and di is not None:
        out.append(("blood-pressure", {"sys": sy, "dia": di}, t, note, None))
    if not out:
        out.append((None, {}, t, note, "No recognised reading in this row (use columns such as type,value,time)"))
    return out

def parse_time(raw):
    """Device time -> naive local datetime, or None if it cannot be understood. No time means now."""
    if raw in (None, ""):
        return datetime.now().replace(second=0, microsecond=0)
    try:
        if isinstance(raw, (int, float)) or re.fullmatch(r"\d{9,13}(\.\d+)?", str(raw).strip()):
            x = float(raw)
            return datetime.fromtimestamp(x / 1000 if x > 1e11 else x).replace(second=0, microsecond=0)
        s = str(raw).strip()
        try:
            t = datetime.fromisoformat(s.replace("Z", "+00:00"))
        except ValueError:
            t = next((datetime.strptime(s, f) for f in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M") if _ok(s, f)), None)
            if t is None:
                return None
        if t.tzinfo:
            t = t.astimezone().replace(tzinfo=None)
        return t.replace(second=0, microsecond=0)
    except (ValueError, OverflowError, OSError):
        return None

def _ok(s, f):
    try:
        datetime.strptime(s, f)
        return True
    except ValueError:
        return False

def build_row(defn, vals, when, note, source):
    row = {}
    for fd in defn["fields"]:
        try:
            v = float(str(vals.get(fd["key"])).strip())
        except (TypeError, ValueError):
            return None, f'{defn["name"]}: "{fd["label"]}" is missing or not a number'
        if not math.isfinite(v) or not fd["lo"] <= v <= fd["hi"]:
            return None, f'{defn["name"]}: {v:g} is outside {fd["lo"]:g} to {fd["hi"]:g} {fd["unit"]} (check the device)'
        row[fd["key"]] = round(v, 2)
    t = parse_time(when)
    if t is None:
        return None, f'{defn["name"]}: the time "{when}" could not be read'
    if t > datetime.now() + timedelta(minutes=5):
        return None, f'{defn["name"]}: the time {t:%Y-%m-%d %H:%M} is in the future (check the device clock)'
    row.update(id=uuid.uuid4().hex[:8], time=t.strftime("%Y-%m-%dT%H:%M"), note=note.strip()[:100], source=source)
    return row, None

def ingest(records, source):
    """Store readings from a device. Returns a summary dict. Never raises for bad readings."""
    source = (source or "Device").strip()[:40] or "Device"
    summary = dict(time=datetime.now().isoformat(timespec="seconds"), source=source, added=0, duplicate=0, rejected=[], by={})
    with LOCK:
        d = load()
        seen = {slug: {(r["time"], tuple(r.get(f["key"]) for f in READINGS[slug]["fields"])) for r in d["readings"].get(slug, [])}
                for slug in READINGS}
        for rec in records[:MAX_RECORDS]:
            for slug, vals, when, note, err in normalize(rec):
                if err:
                    summary["rejected"].append(err)
                    continue
                defn = READINGS[slug]
                row, err = build_row(defn, vals, when, note, source)
                if err:
                    summary["rejected"].append(err)
                    continue
                key = (row["time"], tuple(row[f["key"]] for f in defn["fields"]))
                if key in seen[slug]:
                    summary["duplicate"] += 1
                    continue
                seen[slug].add(key)
                d["readings"][slug] = sorted(d["readings"].get(slug, []) + [row], key=lambda r: r["time"])[-500:]
                summary["added"] += 1
                summary["by"][defn["name"]] = summary["by"].get(defn["name"], 0) + 1
                recent = datetime.strptime(row["time"], "%Y-%m-%dT%H:%M") > datetime.now() - timedelta(hours=24)
                if status_of(defn, row) == "red" and recent:  # same rule as typed-in readings
                    d["alerts"] = (d["alerts"] + [{"time": datetime.now().isoformat(timespec="seconds"),
                                                   "readings": [f'{defn["name"]} {fmt_value(defn, row)} {unit_of(defn)} (from {source})']}])[-50:]
        if len(records) > MAX_RECORDS:
            summary["rejected"].append(f"Only the first {MAX_RECORDS} readings were used")
        summary["rejected_count"] = len(summary["rejected"])
        summary["rejected"] = summary["rejected"][:8]
        d["syncs"] = (d.get("syncs", []) + [summary])[-30:]
        save(d)
    return summary

def parse_file(name, data):
    """CSV or JSON bytes -> (records, device name or None). Raises ValueError with a friendly message."""
    text = data.decode("utf-8-sig", errors="replace")
    ext = Path(name).suffix.lower()
    if ext == ".json":
        try:
            obj = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"{name} is not valid JSON ({e.msg})")
        device = obj.get("device") if isinstance(obj, dict) else None
        if isinstance(obj, dict) and "readings" in obj:
            obj = obj["readings"]
        if isinstance(obj, dict):
            obj = [obj]
        if not isinstance(obj, list):
            raise ValueError(f"{name}: expected a list of readings")
        return obj, device
    if ext in (".csv", ".txt", ".tsv"):
        try:
            dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
        if not rows:
            raise ValueError(f"{name} has no rows")
        return rows, None
    raise ValueError(f"{name}: use a .csv or .json file")

def describe(s):
    bits = ", ".join(f"{n} {k}" for k, n in s["by"].items()) or "nothing new"
    return f'{s["added"]} added ({bits}), {s["duplicate"]} already saved, {s.get("rejected_count", len(s["rejected"]))} rejected'

# ------------------------------------------------------------------ routes
def token_ok():
    token = os.environ.get("ZENAUT_DEVICE_TOKEN", "")
    return not token or hmac.compare_digest(request.headers.get("X-Device-Token", "").encode(), token.encode())

@physical.route("/api/sync", methods=["POST"])
def api_sync():
    if not token_ok():
        return jsonify(ok=False, error="Missing or wrong X-Device-Token"), 403
    body = request.get_json(silent=True)
    if body is None:
        return jsonify(ok=False, error="Send JSON: {\"device\": \"name\", \"readings\": [...]}"), 400
    device = body.get("device") if isinstance(body, dict) else None
    records = body.get("readings") if isinstance(body, dict) else body
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return jsonify(ok=False, error="\"readings\" must be a list"), 400
    s = ingest(records, device or "Gateway")
    return jsonify(ok=True, added=s["added"], duplicate=s["duplicate"], rejected=s["rejected"], summary=describe(s))

@physical.route("/devices/import", methods=["POST"])
def devices_import():
    files = [f for f in request.files.getlist("files") if f and f.filename]
    label = request.form.get("device", "").strip()
    last, errors = None, []
    for f in files:
        try:
            records, dev = parse_file(f.filename, f.read())
        except ValueError as e:
            errors.append(str(e))
            continue
        last = ingest(records, label or dev or f"File {Path(f.filename).name}")
    return redirect(url_for("physical.devices", done="1" if last else None, err="; ".join(errors) or None))

@physical.route("/devices/folder", methods=["POST"])
def devices_folder():
    INBOX.mkdir(exist_ok=True)
    errors, n = [], 0
    for p in sorted(INBOX.iterdir()):
        if not p.is_file() or p.suffix.lower() not in (".csv", ".json", ".txt", ".tsv"):
            continue
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            records, dev = parse_file(p.name, p.read_bytes())
            ingest(records, dev or f"Folder: {p.name}")
            dest = INBOX / "processed"
            n += 1
        except ValueError as e:
            errors.append(str(e))
            dest = INBOX / "failed"
        dest.mkdir(exist_ok=True)
        p.replace(dest / f"{stamp}_{p.name}")
    return redirect(url_for("physical.devices", done="1" if n else None, err="; ".join(errors) or ("No files waiting in the device folder." if not n else None)))

@physical.route("/devices/template.csv")
def devices_template():
    text = ("type,value,sys,dia,time,note\n"
            "heart-rate,64,,,2026-01-15 07:30,resting\n"
            "blood-oxygen,98,,,2026-01-15 07:30,\n"
            "blood-pressure,,118,76,2026-01-15 07:35,\n"
            "blood-sugar,92,,,2026-01-15 07:40,fasting\n"
            "radiation,0.42,,,2026-01-15 20:00,\n")
    return Response(text, mimetype="text/csv", headers={"Content-Disposition": "attachment; filename=zenaut_device_template.csv"})

CSS = """
.wb .bt{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(250px,1fr))}
.wb .bt .card{margin-top:0}
.wb .bt .live{font-size:2rem;font-weight:800;min-height:1.3em}
.wb .bt .msg{font-size:.82rem;color:var(--grey);min-height:2.4em;margin:.3rem 0}
.wb .bt .row{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-top:.5rem}
.wb pre{background:var(--space);border:1px solid rgba(174,178,177,.3);border-radius:10px;padding:.7rem;overflow-x:auto;font-size:.8rem;margin:.5rem 0 0}
.wb input[type=file]{padding:.6rem}
"""

@physical.route("/devices")
def devices():
    d = load()
    syncs = d.get("syncs", [])
    note = ""
    if request.args.get("err"):
        note += f'<div class="alert red">{escape(request.args["err"])}</div>'
    if request.args.get("done") and syncs:
        s = syncs[-1]
        cls = "orange" if s.get("rejected_count") else "green"
        note += f'<div class="alert {cls}"><b>Sync finished.</b> {escape(describe(s))}.'
        if s["rejected"]:
            note += "<ul class=\"clean\">" + "".join(f"<li>{escape(r)}</li>" for r in s["rejected"]) + "</ul>"
        note += "</div>"
    waiting = len([p for p in INBOX.iterdir() if p.is_file() and p.suffix.lower() in (".csv", ".json", ".txt", ".tsv")]) if INBOX.exists() else 0
    hist = "".join(f'<tr><td>{escape(s["time"][:16].replace("T", " "))}</td><td>{escape(s["source"])}</td><td>{s["added"]}</td><td>{s["duplicate"]}</td>'
                   f'<td>{s.get("rejected_count", len(s["rejected"]))}</td></tr>' for s in reversed(syncs[-10:])) \
        or '<tr><td colspan="5" class="small">Nothing synced yet.</td></tr>'
    api = url_for("physical.api_sync", _external=True)
    token = "  -H \"X-Device-Token: YOUR_TOKEN\" \\\n" if os.environ.get("ZENAUT_DEVICE_TOKEN") else ""
    sample = ('{"device": "Ward monitor", "readings": [\n  {"type": "heart-rate", "value": 64, "time": "2026-01-15T07:30:00"},\n'
              '  {"type": "blood-pressure", "sys": 118, "dia": 76},\n  {"type": "blood-oxygen", "value": 98}\n]}')
    body = (note +
            '<div class="card"><h2>Sync readings from your hardware</h2>'
            '<p>Readings from devices go into the same tabs as the ones you type in (heart rate, blood oxygen, blood pressure, blood sugar, hydration, radiation, bone density), '
            'with the same safe ranges, alerts and graphs. Each synced reading is labelled with its device. Syncing twice never creates duplicates. Typing a reading by hand still works exactly as before.</p></div>'
            '<h2 style="margin-top:1.4rem">1. Bluetooth devices</h2>'
            f'<div id="bt-root" data-sync="{url_for("physical.api_sync")}"></div>'
            '<h2 style="margin-top:1.4rem">2. Import a file from a device</h2>'
            f'<form method="post" action="{url_for("physical.devices_import")}" enctype="multipart/form-data" class="card">'
            '<label for="dfiles">CSV or JSON file(s) exported by a device or its app</label><input id="dfiles" type="file" name="files" accept=".csv,.json,.txt,.tsv" multiple required>'
            '<label for="ddev" style="margin-top:.6rem">Device name (optional)</label><input id="ddev" name="device" maxlength="40" placeholder="e.g. Wrist monitor">'
            '<div class="row" style="margin-top:.8rem"><button class="btn primary">Import and sync</button>'
            f'<a class="btn" href="{url_for("physical.devices_template")}">Download example CSV</a></div>'
            '<p class="small" style="margin-top:.6rem">Columns: <code>type</code> (heart-rate, blood-oxygen, blood-pressure, blood-sugar, hydration, radiation, bone-density), '
            '<code>value</code> (or <code>sys</code> and <code>dia</code> for blood pressure), <code>time</code>, optional <code>note</code>. '
            'A file with one column per measurement (<code>heart_rate</code>, <code>spo2</code>, <code>systolic</code>, <code>diastolic</code>, <code>glucose</code> ...) also works.</p></form>'
            '<h2 style="margin-top:1.4rem">3. Device folder</h2>'
            f'<form method="post" action="{url_for("physical.devices_folder")}" class="card">'
            f'<p>Drop CSV or JSON files from your devices into the <code>device_inbox</code> folder next to <code>app.py</code>, then press the button. '
            f'<b>{waiting}</b> file(s) waiting. Synced files move to <code>device_inbox/processed</code>.</p>'
            '<div class="row"><button class="btn primary">Sync from device folder</button></div></form>'
            '<h2 style="margin-top:1.4rem">4. Gateway or bridge program (API)</h2>'
            f'<div class="card"><p>A program that reads your hardware can send readings here at any time.</p><pre>curl -X POST "{escape(api)}" \\\n  -H "Content-Type: application/json" \\\n{escape(token)}  -d \'{escape(sample)}\'</pre>'
            '<p class="small" style="margin-top:.6rem">To require a password for this address, set the <code>ZENAUT_DEVICE_TOKEN</code> environment variable before starting ZENAUT and send it as the <code>X-Device-Token</code> header.</p></div>'
            f'<div class="card"><h2>Sync history</h2><div style="overflow-x:auto"><table><tr><th>When</th><th>Source</th><th>Added</th><th>Already saved</th><th>Rejected</th></tr>{hist}</table></div></div>'
            f'<script src="{url_for("static", filename="js/devices.js")}"></script>')
    return page(f"<style>{CSS}</style>{body}", "devices")
