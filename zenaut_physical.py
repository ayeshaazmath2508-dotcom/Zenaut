"""ZENAUT · Physical health, its own page on the dashboard (Flask blueprint).

One tab per reading (heart rate, blood oxygen, blood pressure, blood sugar, hydration,
radiation, bone density). Readings are typed in by hand (or synced from hardware devices on the Sync devices tab), shown in green / orange / red against
the safe ranges below, and graphed over time. Red readings are recorded as alerts.
Everything is saved on this device in physical_data.json (works offline).
The safe ranges are prototype placeholders: confirm them with your flight surgeon or NASA's
Human Research Program before real use. Not a medical device.
"""
import json
import math
import uuid
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

physical = Blueprint("physical", __name__, url_prefix="/physical")
DATA = Path(__file__).with_name("physical_data.json")
LABEL = {"green": "Safe", "orange": "Monitoring", "red": "Danger"}
ORDER = {"green": 0, "orange": 1, "red": 2}

def field(key, label, unit, lo, hi, step, safe, watch):
    """lo/hi are the limits accepted when typing (they catch typos), safe/watch are status ranges."""
    return {"key": key, "label": label, "unit": unit, "lo": lo, "hi": hi, "step": step, "safe": safe, "watch": watch}

# slug -> reading definition (the order here is the order of the tabs)
READINGS = {
    "heart-rate": {"name": "Heart rate", "fields": [field("value", "Beats per minute", "bpm", 20, 250, 1, (50, 100), (40, 120))],
                   "hint": "Resting heart rate is easiest to compare from day to day."},
    "blood-oxygen": {"name": "Blood oxygen", "fields": [field("value", "SpO2", "%", 50, 100, 0.1, (95, 100), (90, 100))],
                     "hint": "Pulse oximeter reading."},
    "blood-pressure": {"name": "Blood pressure", "fields": [field("sys", "Systolic (top number)", "mmHg", 50, 260, 1, (90, 129), (80, 139)),
                                                              field("dia", "Diastolic (bottom number)", "mmHg", 30, 160, 1, (60, 84), (50, 89))],
                       "hint": "Sit quietly for five minutes before measuring."},
    "blood-sugar": {"name": "Blood sugar", "fields": [field("value", "Glucose", "mg/dL", 20, 600, 1, (70, 130), (60, 180))],
                    "hint": "Add a note such as fasting or after a meal; the safe range is the same for both here."},
    "hydration": {"name": "Hydration", "fields": [field("value", "Hydration", "%", 0, 100, 1, (80, 100), (65, 100))],
                  "hint": "From your hydration monitor or estimate."},
    "radiation": {"name": "Radiation", "fields": [field("value", "Dose today", "mSv", 0, 50, 0.01, (0, 0.8), (0, 1.5))],
                  "hint": "Total dose for the day from your dosimeter."},
    "bone-density": {"name": "Bone density", "fields": [field("value", "T-score", "T-score", -6, 6, 0.01, (-1.0, 3), (-2.5, 3))],
                     "hint": "Bone density changes slowly, so one entry per scan is enough."},
}

CSS = """
.wb .subnav{margin:.6rem 0 0}.wb .subnav a{font-size:.75rem;padding:.3rem .8rem}
.wb .ptile{display:block;background:var(--panel);color:var(--text);border:1px solid rgba(174,178,177,.25);border-left:6px solid var(--s,var(--grey));border-radius:14px;padding:.8rem 1rem;text-decoration:none}
.wb a.ptile:hover{border-color:var(--grey);border-left-color:var(--s,var(--grey))}
.wb .ptile.green{--s:var(--ok)}.wb .ptile.orange{--s:var(--warn)}.wb .ptile.red{--s:var(--bad)}
.wb .ptile .lbl{color:var(--grey);font-size:.82rem}
.wb .ptile .val{font-size:1.8rem;font-weight:700;line-height:1.2}.wb .ptile .val small{font-size:.85rem;font-weight:400;color:var(--grey)}
.wb .ptile .st{font-size:.82rem;font-weight:700;color:var(--s,var(--grey))}.wb .ptile .rng{color:var(--grey);font-size:.78rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
.wb .banner2{padding:.8rem 1rem;border-radius:12px;border:1px solid;margin-top:1rem}
.wb .banner2.green{border-color:var(--ok)}.wb .banner2.orange{border-color:var(--warn)}.wb .banner2.none{border-color:var(--grey)}
.wb .banner2.red{border-color:var(--bad);background:var(--bad);color:#fff}
.wb .list{list-style:none;margin:0;padding:0}.wb .list li{padding:.45rem 0;border-bottom:1px solid rgba(174,178,177,.2);font-size:.9rem}
.wb .list form{display:inline;float:right}
.wb .btn.small{padding:.15rem .7rem;font-size:.75rem}
.wb .band{fill:var(--ok);opacity:.13}
"""

# ------------------------------------------------------------------ data
def load():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("readings", {})
    d.setdefault("alerts", [])
    return d

def save(d):
    DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")

def level(v, safe, watch):
    if safe[0] <= v <= safe[1]:
        return "green"
    return "orange" if watch[0] <= v <= watch[1] else "red"

def status_of(defn, r):
    """Worst status across the reading's fields (blood pressure uses both numbers)."""
    return max((level(r[f["key"]], f["safe"], f["watch"]) for f in defn["fields"]), key=ORDER.get)

def fmt_value(defn, r):
    return "/".join(f'{r[f["key"]]:g}' for f in defn["fields"])

def unit_of(defn):
    return defn["fields"][0]["unit"]

def range_text(defn):
    return " and ".join(f'{f["safe"][0]:g} to {f["safe"][1]:g}' for f in defn["fields"])

def series_of(d, slug):
    return sorted(d["readings"].get(slug, []), key=lambda r: r["time"])

def when(r):
    return r["time"][:16].replace("T", " ")

ADVICE = {
    "green": "In the safe range.",
    "orange": "Monitoring. Rest, check again in a few minutes, and tell the crew medical officer if it stays outside the safe range.",
    "red": "Danger. Stop activity, sit down, and contact the crew medical officer now.",
}

# ------------------------------------------------------------------ chart
def trend_chart(defn, rows):
    W, H, L, R, T, B = 640, 230, 46, 12, 14, 34
    rows = rows[-30:]
    fields = defn["fields"]
    vals = [r[f["key"]] for r in rows for f in fields]
    lo = min(vals + [fields[0]["safe"][0]])
    hi = max(vals + [fields[0]["safe"][1]])
    pad = (hi - lo) * 0.08 or 1
    lo, hi = lo - pad, hi + pad
    n = len(rows)
    X = lambda i: L + ((W - L - R) * i / (n - 1) if n > 1 else (W - L - R) / 2)
    Y = lambda v: T + (hi - v) / (hi - lo) * (H - T - B)
    s = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="{escape(defn["name"])} over time">']
    sa, sb = fields[0]["safe"]
    top, bot = Y(min(sb, hi)), Y(max(sa, lo))
    s.append(f'<rect class="band" x="{L}" y="{top:.1f}" width="{W-L-R}" height="{max(bot-top, 0):.1f}"/>')
    for k in range(5):
        v = lo + (hi - lo) * k / 4
        s.append(f'<line class="axis" x1="{L}" x2="{W-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                 f'<text x="{L-6}" y="{Y(v)+4:.1f}" text-anchor="end">{v:.4g}</text>')
    step = max(1, math.ceil(n / 6))
    for i in range(0, n, step):
        s.append(f'<text x="{X(i):.1f}" y="{H-12}" text-anchor="middle">{rows[i]["time"][5:10]}</text>')
    for j, f in enumerate(fields):
        s.append(f'<polyline class="line l{j}" points="' + " ".join(f"{X(i):.1f},{Y(r[f['key']]):.1f}" for i, r in enumerate(rows)) + '"/>')
        for i, r in enumerate(rows):
            s.append(f'<circle class="dot d{j}" cx="{X(i):.1f}" cy="{Y(r[f["key"]]):.1f}" r="4"><title>{when(r)}: {r[f["key"]]:g} {f["unit"]}</title></circle>')
    s.append("</svg>")
    legend = ""
    if len(fields) > 1:
        legend = '<div class="legend">' + "".join(f'<span><i class="l{j}" style="background:var(--c{j})"></i>{escape(f["label"])}</span>' for j, f in enumerate(fields)) + "</div>"
    return "".join(s) + legend + '<p class="small">The green band is the safe range.</p>'

# ------------------------------------------------------------------ shell
def page(body, active):
    items = ([("overview", "Overview", url_for("physical.dashboard"))] + [(k, v["name"], url_for("physical.reading", slug=k)) for k, v in READINGS.items()]
             + [("devices", "Sync devices", url_for("physical.devices"))])
    sub = "".join(f'<a class="{"on" if k == active else ""}" href="{u}">{escape(t)}</a>' for k, t, u in items)
    return render_template("wellbeing.html", title="Enter and track your readings · stored on this device", links="",
                           body=Markup(f'<style>{CSS}</style><nav class="subnav">{sub}</nav>{body}'),
                           css=Markup(WB_CSS), heading="Physical health", active="physical")

# ------------------------------------------------------------------ pages
@physical.route("/")
def dashboard():
    d = load()
    tiles, worst, named, any_reading = "", "green", {"red": [], "orange": []}, False
    for slug, defn in READINGS.items():
        rows = series_of(d, slug)
        link = url_for("physical.reading", slug=slug)
        if not rows:
            tiles += (f'<a class="ptile" href="{link}"><div class="lbl">{escape(defn["name"])}</div><div class="val">--</div>'
                      f'<div class="rng">No reading yet. Tap to add one.</div></a>')
            continue
        any_reading = True
        r = rows[-1]
        st = status_of(defn, r)
        if st != "green":
            named[st].append(defn["name"])
        tiles += (f'<a class="ptile {st}" href="{link}"><div class="lbl">{escape(defn["name"])}</div>'
                  f'<div class="val">{fmt_value(defn, r)} <small>{escape(unit_of(defn))}</small></div>'
                  f'<div class="st">{LABEL[st]}</div><div class="rng">{when(r)} | safe {range_text(defn)}</div></a>')
    if named["red"]:
        banner = f'<div class="banner2 red"><b>Danger: {escape(", ".join(named["red"]))}.</b> {ADVICE["red"]}</div>'
    elif named["orange"]:
        banner = f'<div class="banner2 orange">Needs monitoring: {escape(", ".join(named["orange"]))}.</div>'
    elif any_reading:
        banner = '<div class="banner2 green">All of your latest readings are in the safe range.</div>'
    else:
        banner = '<div class="banner2 none">No readings yet. Choose a tab above and add your first reading.</div>'
    alerts = "".join(f'<li><b>{escape(a["time"][:16].replace("T", " "))}</b> &nbsp; {escape(", ".join(a["readings"]))}</li>' for a in reversed(d["alerts"][-5:])) \
        or '<li class="small">No red alerts recorded.</li>'
    body = (banner + '<p class="small" style="margin-top:.6rem">Tiles show your latest reading for each measurement.</p>'
            + f'<div class="grid" style="margin-top:.6rem">{tiles}</div>'
            + f'<div class="card"><h2>Recent red alerts</h2><ul class="list">{alerts}</ul>'
            + '<p class="small" style="margin-top:.6rem">There is no live link to a medical team yet. Alerts are kept here for review.</p></div>'
            + '<div class="grid"><div class="card"><h2>Nutrition</h2><p>Aim for protein at every meal, calcium and vitamin D for bone health, and a few extra fluids each hour.</p></div>'
            + '<div class="card"><h2>Exercise</h2><p>Do 2 hours of resistance and cardio each day in microgravity to protect bone and muscle.</p></div>'
            + '<div class="card"><h2>If you feel seriously ill</h2><p>Tell the crew medical officer now. ZENAUT also records red readings so the flight surgeon can see them.</p></div></div>')
    return page(body, "overview")

def reading_page(slug, error=None, code=200):
    defn = READINGS[slug]
    d = load()
    rows = series_of(d, slug)
    now = datetime.now().strftime("%Y-%m-%dT%H:%M")
    out = f'<h2 style="margin-top:1.2rem">{escape(defn["name"])}</h2>'
    if rows:
        r = rows[-1]
        st = status_of(defn, r)
        out += (f'<div class="card"><h2>Latest reading</h2><div class="stat">{fmt_value(defn, r)} <span class="small">{escape(unit_of(defn))}</span> '
                f'<span class="badge {st}">{LABEL[st].upper()}</span></div>'
                f'<div class="alert {st}">{ADVICE[st]}</div>'
                f'<p class="small" style="margin-top:.6rem">{when(r)} | safe range {range_text(defn)} {escape(unit_of(defn))}</p></div>')
    if error:
        out += f'<div class="alert red" style="margin-top:1rem">{escape(error)}</div>'
    inputs = "".join(
        f'<div><label for="f-{f["key"]}">{escape(f["label"])} ({escape(f["unit"])})</label>'
        f'<input id="f-{f["key"]}" name="{f["key"]}" type="number" inputmode="decimal" step="{f["step"]}" min="{f["lo"]}" max="{f["hi"]}" required></div>'
        for f in defn["fields"])
    out += (f'<form method="post" class="card" action="{url_for("physical.add_reading", slug=slug)}"><h2>Add a reading</h2>'
            f'<p class="small">{escape(defn["hint"])}</p><div class="grid">{inputs}'
            f'<div><label for="f-time">Date and time</label><input id="f-time" name="time" type="datetime-local" value="{now}" max="{now}" required></div></div>'
            '<label for="f-note" style="margin-top:.8rem">Note (optional)</label><input id="f-note" name="note" maxlength="100" placeholder="e.g. after exercise">'
            '<div class="row"><button class="btn primary">Save reading</button></div></form>')
    if rows:
        out += f'<div class="card"><h2>Trend</h2>{trend_chart(defn, rows)}</div>'
        items = ""
        for r in reversed(rows[-20:]):
            st = status_of(defn, r)
            note = f' &nbsp; <span class="small">{escape(r["note"])}</span>' if r.get("note") else ""
            if r.get("source"):
                note += f' &nbsp; <span class="small">synced from {escape(r["source"])}</span>'
            items += (f'<li><span class="badge {st}">{LABEL[st]}</span> &nbsp; <b>{fmt_value(defn, r)}</b> {escape(unit_of(defn))} &nbsp; {when(r)}{note}'
                      f'<form method="post" action="{url_for("physical.delete_reading", slug=slug)}" onsubmit="return confirm(\'Delete this reading?\')">'
                      f'<input type="hidden" name="id" value="{escape(r["id"])}"><button class="btn small">Delete</button></form></li>')
        out += f'<div class="card"><h2>History</h2><ul class="list">{items}</ul></div>'
    else:
        out += '<p class="small" style="margin-top:1rem">No readings yet. Add your first one above.</p>'
    return page(out, slug), code

@physical.route("/<slug>")
def reading(slug):
    if slug not in READINGS:
        abort(404)
    return reading_page(slug)[0]

def parse(defn, f):
    row = {}
    for fd in defn["fields"]:
        try:
            v = float(f.get(fd["key"], ""))
        except ValueError:
            return None, f'Enter a number for "{fd["label"]}".'
        if not math.isfinite(v) or not fd["lo"] <= v <= fd["hi"]:
            return None, f'"{fd["label"]}" should be between {fd["lo"]:g} and {fd["hi"]:g} {fd["unit"]}. Check for a typing mistake.'
        row[fd["key"]] = round(v, 2)
    try:
        t = datetime.fromisoformat(f.get("time", ""))
    except ValueError:
        return None, "Choose a valid date and time."
    if t > datetime.now() + timedelta(minutes=1):
        return None, "The date and time cannot be in the future."
    row.update(id=uuid.uuid4().hex[:8], time=t.strftime("%Y-%m-%dT%H:%M"), note=(f.get("note") or "").strip()[:100])
    return row, None

@physical.route("/<slug>", methods=["POST"])
def add_reading(slug):
    if slug not in READINGS:
        abort(404)
    defn = READINGS[slug]
    row, err = parse(defn, request.form)
    if err:
        return reading_page(slug, error=err, code=400)
    d = load()
    d["readings"][slug] = sorted(d["readings"].get(slug, []) + [row], key=lambda r: r["time"])[-500:]
    if status_of(defn, row) == "red":  # serious reading: record it for the medical team (no live link yet)
        d["alerts"] = (d["alerts"] + [{"time": datetime.now().isoformat(timespec="seconds"),
                                       "readings": [f'{defn["name"]} {fmt_value(defn, row)} {unit_of(defn)}']}])[-50:]
    save(d)
    return redirect(url_for("physical.reading", slug=slug))

@physical.route("/<slug>/delete", methods=["POST"])
def delete_reading(slug):
    if slug not in READINGS:
        abort(404)
    d = load()
    rid = request.form.get("id", "")
    d["readings"][slug] = [r for r in d["readings"].get(slug, []) if r.get("id") != rid]
    save(d)
    return redirect(url_for("physical.reading", slug=slug))

# ------------------------------------------------------------------ header status chip
def overall_status():
    """Colour and text for the header chip, from the latest reading of each measurement."""
    d = load()
    red, orange, any_reading = [], [], False
    for slug, defn in READINGS.items():
        rows = series_of(d, slug)
        if not rows:
            continue
        any_reading = True
        st = status_of(defn, rows[-1])
        if st == "red":
            red.append(defn["name"])
        elif st == "orange":
            orange.append(defn["name"])
    if red:
        return {"level": "alert", "label": "Danger: " + ", ".join(red)}
    if orange:
        return {"level": "watch", "label": "Monitoring: " + ", ".join(orange)}
    if any_reading:
        return {"level": "ok", "label": "All readings in range"}
    return {"level": "ok", "label": "No readings yet"}

import zenaut_devices  # noqa: E402,F401  (adds the Sync devices tab; must stay at the end of this file)
