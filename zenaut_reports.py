"""
ZENAUT · Your wellbeing: daily graphs + weekly report (Flask blueprint, Python + CSS in one file).

Registered in app.py, so it lives inside the ZENAUT shell at /wellbeing.
Graphs are drawn as SVG by this file, so nothing is downloaded and it works offline.

It syncs automatically with the other tabs (Physical health, Mood Monitor, Stress check, Sleep, Journal): see
zenaut_sync.py. Other programs can still add daily values:
    POST /wellbeing/api/log   (JSON, e.g. {"mood": 7, "stress": 30, "hydration_l": 2.4, ...})
"""
import random
import sqlite3
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from flask import Blueprint, Response, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup

reports = Blueprint("reports", __name__, url_prefix="/wellbeing")

DB = Path(__file__).with_name("zenaut_data.db")
OUTBOX = Path(__file__).with_name("outbox")      # reports wait here until a sync/manual upload
FIELDS = ["mood", "stress", "sleep_score", "sleep_hours", "hydration_l",
          "heart_rate", "spo2", "systolic", "diastolic", "radiation_msv", "blood_sugar"]

# ============================================================== NASA palette CSS
CSS = r"""
.wb{--blue:#0B3D91;--red:#FC3D21;--grey:#AEB2B1;
 --space:#07102A;--panel:#0F1B3D;--panel2:#152550;--text:#EAF0FF;
 --ok:#3DDB85;--warn:#FF9E1B;--bad:#FC3D21;
 --c0:#5FA8FF;--c1:#FFFFFF;--c2:#AEB2B1;--c3:#7FD6F5;
 color:var(--text);max-width:1000px;padding-top:22px;line-height:1.5}
:root[data-theme="warm"] .wb{--space:#F1E8D8;--panel:#FFFFFF;--panel2:#F6EEDF;--text:#1B2540;--grey:#5C6578;
 --ok:#1E9E5A;--warn:#D97A00;--c0:#0B3D91;--c1:#FC3D21;--c2:#6B7488;--c3:#1E8FB5}
.wb>h1{font-size:32px;margin:0 0 4px}
.wb .tag{color:var(--grey);font-size:.85rem}
.wb nav{display:flex;gap:.6rem;flex-wrap:wrap;margin-top:.8rem}
.wb nav a,.wb .btn{padding:.45rem 1rem;border-radius:999px;border:1px solid var(--grey);color:var(--text);
 text-decoration:none;font-size:.8rem;background:transparent;cursor:pointer;display:inline-block;font-weight:600}
.wb nav a.on,.wb .btn.primary{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
.wb h2{font-size:1rem;margin:0 0 .7rem;display:flex;gap:.5rem;align-items:center}
.wb h2::before{content:"";width:6px;height:1.1em;background:var(--red);border-radius:3px}
.wb .card{background:var(--panel);border:1px solid rgba(174,178,177,.25);border-radius:16px;padding:1.1rem;margin-top:1rem}
.wb .grid{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(220px,1fr))}
.wb .grid .card{margin-top:0}
.wb .stat{font-size:2.1rem;font-weight:800}.wb .small{font-size:.85rem;color:var(--grey)}
.wb .badge{display:inline-block;padding:.15rem .7rem;border-radius:999px;font-size:.78rem;font-weight:700;color:#07102A}
.wb .badge.green{background:var(--ok)}.wb .badge.orange{background:var(--warn)}.wb .badge.red{background:var(--bad);color:#fff}
.wb .alert{border-left:6px solid;padding:.6rem .9rem;border-radius:10px;background:var(--space);margin-top:.5rem}
.wb .alert.green{border-color:var(--ok)}.wb .alert.orange{border-color:var(--warn)}.wb .alert.red{border-color:var(--bad)}
.wb .meter{height:10px;background:var(--space);border-radius:99px;overflow:hidden;margin-top:.5rem}
.wb .meter i{display:block;height:100%;border-radius:99px}
.wb .meter .green{background:var(--ok)}.wb .meter .orange{background:var(--warn)}.wb .meter .red{background:var(--bad)}
.wb table{width:100%;border-collapse:collapse;font-size:.9rem}
.wb th,.wb td{padding:.45rem .4rem;text-align:left;border-bottom:1px solid rgba(174,178,177,.2)}
.wb th{color:var(--grey);font-size:.8rem}
.wb svg.chart{width:100%;height:auto;display:block}
.wb svg .axis{stroke:var(--grey);stroke-opacity:.35}.wb svg text{fill:var(--grey);font-size:11px}
.wb svg .line{fill:none;stroke-width:2.5;stroke-linejoin:round}.wb svg .dot{stroke:var(--space);stroke-width:1.5}
.wb .l0{stroke:var(--c0)}.wb .l1{stroke:var(--c1)}.wb .l2{stroke:var(--c2)}.wb .l3{stroke:var(--c3)}
.wb .d0{fill:var(--c0)}.wb .d1{fill:var(--c1)}.wb .d2{fill:var(--c2)}.wb .d3{fill:var(--c3)}
.wb .legend{display:flex;gap:1rem;flex-wrap:wrap;font-size:.8rem;margin-top:.4rem}
.wb .legend i{display:inline-block;width:18px;height:4px;border-radius:2px;margin-right:.4rem;vertical-align:middle}
.wb form.log{display:grid;gap:.6rem;grid-template-columns:repeat(auto-fit,minmax(130px,1fr))}
.wb label{font-size:.8rem;color:var(--grey);display:block}
.wb input,.wb select{width:100%;margin-top:.2rem;padding:.5rem;border-radius:10px;background:var(--space);color:var(--text);border:1px solid var(--grey);font:inherit}
.wb .banner{background:var(--panel2);border:1px dashed var(--warn);border-radius:12px;padding:.6rem .9rem;margin-top:1rem;font-size:.85rem}
.wb ul.clean{margin-left:1.1rem}
@media print{.side,.top{display:none}body{display:block;background:#fff;color:#000}.main{padding:0}
 .wb,.wb .card{background:#fff;color:#000;border-color:#999}.wb nav,.wb form,.wb .btn{display:none}}
"""

# ============================================================== database
def db():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute("""CREATE TABLE IF NOT EXISTS daily(
        day TEXT PRIMARY KEY, mood REAL, stress REAL, sleep_score REAL, sleep_hours REAL,
        hydration_l REAL, heart_rate REAL, spo2 REAL, systolic REAL, diastolic REAL,
        radiation_msv REAL, blood_sugar REAL, demo INTEGER DEFAULT 0)""")
    return con

def seed_demo(days=21):
    """Fake data so the graphs have something to show. Clearly flagged as demo."""
    rnd = random.Random(7)
    with db() as con:
        if con.execute("SELECT COUNT(*) FROM daily").fetchone()[0]:
            return
        for i in range(days, 0, -1):
            d = (date.today() - timedelta(days=i - 1)).isoformat()
            bad = i in (5, 4)                       # a rough patch for the demo
            con.execute("INSERT INTO daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,1)", (
                d, rnd.uniform(3, 5) if bad else rnd.uniform(6, 9),
                rnd.uniform(55, 75) if bad else rnd.uniform(15, 45),
                rnd.uniform(45, 62) if bad else rnd.uniform(68, 92),
                rnd.uniform(4.8, 5.8) if bad else rnd.uniform(6.5, 8),
                rnd.uniform(1.5, 2.0) if bad else rnd.uniform(2.2, 3.0),
                rnd.uniform(62, 78), rnd.uniform(95, 99), rnd.uniform(108, 124),
                rnd.uniform(68, 80), rnd.uniform(0.45, 0.8), rnd.uniform(80, 120)))

def num(v, default=None):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default

def save_day(data: dict, day=None, demo=0):
    day = day or data.get("day") or date.today().isoformat()
    vals = [num(data.get(f)) for f in FIELDS]
    with db() as con:
        con.execute("INSERT OR REPLACE INTO daily VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (day, *vals, demo))

# ============================================================== scoring
def status(score):
    return "green" if score >= 75 else "orange" if score >= 55 else "red"

LABEL = {"green": "Safe", "orange": "Monitoring", "red": "Danger"}

def avg(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else None

def wavg(pairs):
    """Weighted average of (weight, score) pairs, using only the scores that exist. None if there are none."""
    pairs = [(w, v) for w, v in pairs if v is not None]
    return round(sum(w * v for w, v in pairs) / sum(w for w, _ in pairs)) if pairs else None

def enrich(r):
    """Row -> dict with component scores (0-100). Thresholds are illustrative; tune with your medical team.
    A score uses only the measurements that exist for that day; with no data it is None (shown as "no data")."""
    r = dict(r)
    has = lambda k: r.get(k) is not None
    mood_p = r["mood"] * 10 if has("mood") else None
    calm = 100 - r["stress"] if has("stress") else None
    sleep = r["sleep_score"] if has("sleep_score") else None
    r["mood_p"], r["calm"], r["sleep"] = mood_p, calm, sleep
    r["mental"] = wavg([(.35, mood_p), (.35, calm), (.30, sleep)])

    hyd = min(r["hydration_l"] / 2.5, 1) * 100 if has("hydration_l") else None
    hr_s = bp_s = ox_s = None
    if has("heart_rate"):
        hr = r["heart_rate"]
        hr_s = 100 if 50 <= hr <= 90 else max(0, 100 - (50 - hr if hr < 50 else hr - 90) * 4)
    if has("systolic"):
        sy, di = r["systolic"], (r["diastolic"] if has("diastolic") else 76)
        bp_s = 100 if sy <= 120 and di <= 80 else 85 if sy <= 130 else 65 if sy <= 139 else 40
    if has("spo2"):
        ox = r["spo2"]
        ox_s = 100 if ox >= 97 else 85 if ox >= 95 else 55 if ox >= 92 else 20
    cardio = avg([hr_s, bp_s, ox_s])
    rad_s = bs_s = None
    if has("radiation_msv"):
        rad = r["radiation_msv"]
        rad_s = 100 if rad <= .8 else 75 if rad <= 1.2 else 50 if rad <= 2 else 20
    if has("blood_sugar"):
        bs = r["blood_sugar"]
        bs_s = 100 if 70 <= bs <= 140 else 60 if (60 <= bs < 70 or 140 < bs <= 180) else 20
    r["hydration"], r["cardio"], r["radiation"], r["sugar"] = hyd, cardio, rad_s, bs_s
    r["physical"] = wavg([(.2, hyd), (.35, cardio), (.25, rad_s), (.2, bs_s)])
    r["overall"] = wavg([(1, r["mental"]), (1, r["physical"])])
    r["critical"] = critical_flags(r)
    return r

def critical_flags(r):
    out = []
    if (r.get("spo2") or 99) < 92:        out.append(f"Blood oxygen low ({r['spo2']:.0f}%)")
    if (r.get("systolic") or 118) >= 160 or (r.get("systolic") or 118) < 90:
        out.append(f"Blood pressure out of safe range ({r['systolic']:.0f}/{(r.get('diastolic') or 0):.0f})")
    hr = r.get("heart_rate") or 70
    if hr > 120 or hr < 45:               out.append(f"Heart rate abnormal ({hr:.0f} bpm)")
    bs = r.get("blood_sugar") or 95
    if bs < 60 or bs > 250:               out.append(f"Blood sugar abnormal ({bs:.0f} mg/dL)")
    return out

def get_rows(days, offset=0):
    """Rows for the calendar window of `days` days that ends `offset` days before today (days without data are absent)."""
    end = date.today() - timedelta(days=offset)
    start = end - timedelta(days=days - 1)
    with db() as con:
        rows = con.execute("SELECT * FROM daily WHERE day>=? AND day<=? ORDER BY day", (start.isoformat(), end.isoformat())).fetchall()
    return [enrich(r) for r in rows]

def sync_now():
    """Copy the other tabs into the daily table. Returns the summary, or None if the sync itself failed."""
    try:
        from zenaut_sync import sync_all
        return sync_all()
    except Exception:
        return None

# ============================================================== SVG charts (pure Python)
def line_chart(dates, series, ymin=0, ymax=100, zones=True):
    """series = [(label, [values], css_index)]"""
    W, H, L, R, T, B = 640, 250, 38, 12, 12, 28
    n = len(dates)
    X = lambda i: L + i * (W - L - R) / max(n - 1, 1)
    Y = lambda v: T + (ymax - min(max(v, ymin), ymax)) / (ymax - ymin) * (H - T - B)
    s = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img">']
    if zones:
        for lo, hi, col in ((ymin, 55, "#FC3D21"), (55, 75, "#FF9E1B"), (75, ymax, "#3DDB85")):
            s.append(f'<rect x="{L}" y="{Y(hi):.1f}" width="{W-L-R}" height="{Y(lo)-Y(hi):.1f}" fill="{col}" opacity=".09"/>')
    step = (ymax - ymin) / 4
    for k in range(5):
        v = ymin + k * step
        s.append(f'<line class="axis" x1="{L}" x2="{W-R}" y1="{Y(v):.1f}" y2="{Y(v):.1f}"/>'
                 f'<text x="{L-6}" y="{Y(v)+4:.1f}" text-anchor="end">{v:g}</text>')
    skip = 1 if n <= 8 else 2 if n <= 16 else 4
    for i, d in enumerate(dates):
        if i % skip == 0 or i == n - 1:
            s.append(f'<text x="{X(i):.1f}" y="{H-8}" text-anchor="middle">{d[5:]}</text>')
    for label, vals, c in series:
        run = []                       # days with no value (None) break the line instead of drawing a false zero
        for i, v in enumerate(list(vals) + [None]):
            if v is not None:
                run.append((i, v))
                continue
            if len(run) > 1:
                pts = " ".join(f"{X(j):.1f},{Y(x):.1f}" for j, x in run)
                s.append(f'<polyline class="line l{c}" points="{pts}"><title>{escape(label)}</title></polyline>')
            for j, x in run:
                s.append(f'<circle class="dot d{c}" cx="{X(j):.1f}" cy="{Y(x):.1f}" r="3.5">'
                         f'<title>{escape(label)} {dates[j]}: {x:.1f}</title></circle>')
            run = []
    s.append("</svg>")
    leg = "".join(f'<span><i style="background:var(--c{c})"></i>{escape(l)}</span>' for l, _, c in series)
    return "".join(s) + f'<div class="legend">{leg}</div>'

# ============================================================== page layout
def layout(title, body, active="", theme="dark"):
    nav = [("dashboard", "Daily graphs", "reports.dashboard"), ("weekly", "Weekly report", "reports.weekly"), ("stress", "Stress check", "stress.page")]
    links = "".join(f'<a class="{"on" if k == active else ""}" href="{url_for(e)}">{t}</a>' for k, t, e in nav)
    return render_template("wellbeing.html", title=title, links=Markup(links), body=Markup(body),
                           css=Markup(CSS), active="wellbeing")

def theme_of():
    return "dark"  # theme is set in Profile > Settings

def badge(score):
    st = status(score)
    return f'<span class="badge {st}">{LABEL[st]}</span>'

def score_card(name, score):
    if score is None:
        return (f'<div class="card"><div class="small">{name}</div><div class="stat">--</div>'
                '<span class="small">No data yet for this score</span><div class="meter"></div></div>')
    st = status(score)
    return (f'<div class="card"><div class="small">{name}</div><div class="stat">{score:.0f}'
            f'<span class="small"> /100</span></div>{badge(score)}'
            f'<div class="meter"><i class="{st}" style="width:{score:.0f}%"></i></div></div>')

# ============================================================== data sources card
def sources_card(info):
    """Shows where each measure comes from and how many days it has, plus a Sync now button."""
    try:
        from zenaut_sync import SOURCES
    except Exception:
        return ""
    if info is None:
        status_line = '<div class="alert orange">The automatic sync could not run just now. The graphs show what was saved last time.</div>'
        counts = {}
    else:
        status_line = f'<p class="small">Last synced {escape(info["time"])}. {info["days"]} day(s) of data found in your other tabs.</p>'
        if info["problems"]:
            status_line += "".join(f'<div class="alert orange">{escape(p)}</div>' for p in info["problems"])
        counts = info["counts"]
    rows = "".join(f'<tr><td>{escape(name)}</td><td>{escape(src)}</td><td>{max((counts.get(f, 0) for f in fields), default=0)}</td></tr>'
                   for name, src, fields in SOURCES)
    return ('<div class="card"><h2>Synced from your other tabs</h2>'
            '<p class="small">These graphs and the weekly report are filled in automatically from what you record elsewhere, so nothing needs to be entered twice. '
            'Each day uses the average of that day\'s readings (radiation uses the highest dose). Scores use only the measurements that exist for the day.</p>'
            f'{status_line}<div style="overflow-x:auto"><table><tr><th>Measure</th><th>Comes from</th><th>Days with data</th></tr>{rows}</table></div>'
            f'<form method="post" action="{url_for("reports.sync")}" style="margin-top:.8rem"><button class="btn">Sync now</button></form></div>')

@reports.route("/sync", methods=["POST"])
def sync():
    sync_now()
    return redirect(request.referrer or url_for("reports.dashboard"))

# ============================================================== routes: dashboard
@reports.route("/")
def dashboard():
    info = sync_now()          # copy the other tabs in first, so real data replaces the demo
    seed_demo()
    days = int(request.args.get("days", 7)) if request.args.get("days", "7") in ("7", "14", "30") else 7
    th = theme_of()
    rows = get_rows(days)
    ranges = "".join(f'<a class="{"on" if days == d else ""}" href="?days={d}">{d} days</a>' for d in (7, 14, 30))
    if not rows:
        return layout("Daily graphs", f'<nav>{ranges}</nav><div class="card">No readings in the last {days} days. Record something in '
                      f'Physical health, Mood Monitor, Stress check or Sleep and it appears here by itself. Try a longer range above.</div>'
                      + sources_card(info), "dashboard", th)
    dates = [r["day"] for r in rows]
    today = rows[-1]

    alerts = "".join(f'<div class="alert red"><b>{escape(f)}</b> on {today["day"]}. Notify the flight surgeon / medical team now '
                     f'and follow your emergency medical procedure.</div>' for f in today["critical"])
    with db() as con:
        has_demo = con.execute("SELECT COUNT(*) FROM daily WHERE demo=1").fetchone()[0]
    demo = ('<div class="banner">Showing <b>demo data</b>. Log real values below or from your other modules. '
            f'<form method="post" action="{url_for("reports.clear_demo")}" style="display:inline">'
            '<button class="btn" style="margin-left:.5rem">Clear demo data</button></form></div>') if has_demo else ""

    mental = line_chart(dates, [("Mental score", [r["mental"] for r in rows], 0), ("Mood", [r["mood_p"] for r in rows], 1),
                                ("Sleep", [r["sleep"] for r in rows], 2), ("Calm (100 − stress)", [r["calm"] for r in rows], 3)])
    physical = line_chart(dates, [("Physical score", [r["physical"] for r in rows], 0), ("Hydration", [r["hydration"] for r in rows], 1),
                                  ("Heart & BP & O₂", [r["cardio"] for r in rows], 2), ("Radiation safety", [r["radiation"] for r in rows], 3)])
    overall = line_chart(dates, [("Overall", [r["overall"] for r in rows], 0), ("Mental", [r["mental"] for r in rows], 2),
                                 ("Physical", [r["physical"] for r in rows], 3)])
    vit = [r for r in rows]
    vitals = line_chart(dates, [("Heart rate (bpm)", [r["heart_rate"] for r in vit], 0),
                                ("Systolic BP", [r["systolic"] for r in vit], 2),
                                ("Blood oxygen %", [r["spo2"] for r in vit], 3)], ymin=40, ymax=140, zones=False)

    form = "".join(f'<div><label>{lbl}</label><input name="{k}" type="number" step="any" placeholder="{ph}"></div>' for k, lbl, ph in [
        ("mood", "Mood 1-10", "7"), ("stress", "Stress 0-100", "30"), ("sleep_score", "Sleep score", "80"),
        ("sleep_hours", "Sleep hours", "7"), ("hydration_l", "Water (L)", "2.5"), ("heart_rate", "Heart rate", "68"),
        ("spo2", "Blood O₂ %", "98"), ("systolic", "Systolic", "118"), ("diastolic", "Diastolic", "76"),
        ("radiation_msv", "Radiation mSv", "0.6"), ("blood_sugar", "Sugar mg/dL", "95")])

    body = f"""{demo}{alerts}
<nav>{ranges}</nav>
<div class="grid">{score_card("Mental health · latest", today["mental"])}{score_card("Physical health · latest", today["physical"])}{score_card("Overall · latest", today["overall"])}</div>
<div class="card"><h2>Overall, day by day</h2>{overall}</div>
<div class="card"><h2>Mental health</h2>{mental}</div>
<div class="card"><h2>Physical health</h2>{physical}</div>
<div class="card"><h2>Vitals</h2>{vitals}</div>
{sources_card(info)}
<div class="card"><h2>Log today (manual upload)</h2>
<p class="small">Only needed for something no other tab records. Values from Physical health, Mood Monitor, Stress check, Sleep and Journal are copied in automatically and take priority.</p>
<form class="log" method="post" action="{url_for('reports.log')}">{form}
<button class="btn primary" style="grid-column:1/-1">Save today</button></form></div>"""
    return layout("Daily graphs", body, "dashboard", th)

@reports.route("/log", methods=["POST"])
def log():
    save_day(request.form)
    return redirect(url_for("reports.dashboard"))

@reports.route("/clear-demo", methods=["POST"])
def clear_demo():
    with db() as con:
        con.execute("DELETE FROM daily WHERE demo=1")
    return redirect(url_for("reports.dashboard"))

# ============================================================== routes: JSON feed for other modules
@reports.route("/api/log", methods=["POST"])
def api_log():
    data = request.get_json(silent=True) or {}
    save_day(data)
    return jsonify(ok=True, day=data.get("day") or date.today().isoformat())

@reports.route("/api/data")
def api_data():
    sync_now()
    try:
        days = max(1, min(int(request.args.get("days", 7)), 365))
    except ValueError:
        days = 7
    return jsonify(get_rows(days))

# ============================================================== weekly report
def build_week(days=7):
    cur, prev = get_rows(days), get_rows(days, offset=days)   # this calendar week and the one before it
    keys = ["mental", "physical", "overall", "mood", "stress", "sleep_hours", "hydration_l", "heart_rate", "spo2", "systolic"]
    now = {k: avg([r.get(k) for r in cur]) for k in keys}
    before = {k: avg([r.get(k) for r in prev]) for k in keys} if prev else None
    rad_vals = [r.get("radiation_msv") for r in cur if r.get("radiation_msv") is not None]
    rad_total = sum(rad_vals) if rad_vals else None
    flags = [(r["day"], f) for r in cur for f in r["critical"]]
    low = [r["day"] for r in cur if r["overall"] is not None and r["overall"] < 55]
    tips = []
    if now["sleep_hours"] is not None and now["sleep_hours"] < 7: tips.append(f"Average sleep was {now['sleep_hours']:.1f} h: follow the Sleep Corner wind-down alerts and get morning light.")
    if now["hydration_l"] is not None and now["hydration_l"] < 2.0: tips.append("Hydration was low: schedule fluid reminders through the day.")
    if now["stress"] is not None and now["stress"] > 60: tips.append("Stress was high: use grounding and relaxation exercises, and schedule a call with loved ones.")
    if now["mood"] is not None and now["mood"] < 5: tips.append("Mood was low most days: arrange a check-in with the behavioural health team.")
    if rad_total is not None and rad_total / len(rad_vals) > 1.0: tips.append("Radiation dose is above the usual daily level: review with the flight surgeon.")
    if flags: tips.append("Critical readings occurred: medical team must review them (listed above).")
    if not tips: tips.append("All areas look steady. Keep the current routine." if cur else "No data this week yet.")
    return cur, now, before, rad_total, flags, low, tips

def delta(a, b, good_high=True):
    if a is None or b is None: return ""
    d = a - b
    if abs(d) < .05: return '<span class="small">no change</span>'
    ok = (d > 0) == good_high
    return f'<span style="color:var(--{"ok" if ok else "warn"})">{"▲" if d > 0 else "▼"} {abs(d):.1f}</span>'

def fmt(v, f, unit=""):
    return f.format(v) + unit if v is not None else "—"

@reports.route("/weekly")
def weekly():
    info = sync_now()
    seed_demo()
    th = theme_of()
    cur, now, before, rad, flags, low, tips = build_week()
    if not cur:
        return layout("Weekly report", '<div class="card">No readings in the last 7 days. Record something in Physical health, Mood Monitor, '
                      'Stress check or Sleep and the report fills in by itself.</div>' + sources_card(info), "weekly", th)
    dates = [r["day"] for r in cur]
    ov = now["overall"]
    st = status(ov) if ov is not None else "orange"
    row = lambda name, k, f, good=True: (f'<tr><td>{name}</td><td>{fmt(now[k], f)}</td>'
                                         f'<td>{delta(now[k], before[k], good) if before else ""}</td></tr>')
    table = (row("Mental score", "mental", "{:.0f}") + row("Physical score", "physical", "{:.0f}") + row("Overall score", "overall", "{:.0f}") +
             row("Mood (1-10)", "mood", "{:.1f}") + row("Stress (0-100)", "stress", "{:.0f}", False) +
             row("Sleep (h)", "sleep_hours", "{:.1f}") + row("Hydration (L)", "hydration_l", "{:.1f}") +
             row("Heart rate (bpm)", "heart_rate", "{:.0f}", False) + row("Blood oxygen %", "spo2", "{:.0f}") +
             f'<tr><td>Radiation total (mSv)</td><td>{fmt(rad, "{:.2f}")}</td><td></td></tr>')
    fl = "".join(f'<div class="alert red"><b>{d}</b> · {escape(f)}</div>' for d, f in flags) or '<div class="alert green">No critical readings this week.</div>'
    lowtxt = (f'<div class="alert orange">Days in the danger zone: {", ".join(low)}</div>' if low else "")
    chart = line_chart(dates, [("Overall", [r["overall"] for r in cur], 0), ("Mental", [r["mental"] for r in cur], 2),
                               ("Physical", [r["physical"] for r in cur], 3)])
    head = (f'<div class="stat">{ov:.0f}<span class="small"> /100 overall</span> {badge(ov)}</div>'
            f'<div class="meter"><i class="{st}" style="width:{ov:.0f}%"></i></div>') if ov is not None else \
           '<div class="stat">--<span class="small"> overall score needs more data</span></div>'
    body = f"""
<div class="card"><h2>Week {dates[0]} to {dates[-1]}</h2>
{head}<p class="small">{len(cur)} day(s) with data in the last 7 days.</p></div>
<div class="card"><h2>Trend</h2>{chart}</div>
<div class="card"><h2>Weekly summary <span class="small">(vs previous week)</span></h2>
<table><tr><th>Measure</th><th>This week</th><th>Change</th></tr>{table}</table></div>
<div class="card"><h2>Alerts</h2>{fl}{lowtxt}</div>
<div class="card"><h2>Recommendations</h2><ul class="clean">{"".join(f"<li>{escape(t)}</li>" for t in tips)}</ul></div>
{sources_card(info)}
<div class="card"><h2>Share</h2>
<a class="btn" href="{url_for('reports.weekly_txt')}">Download report (.txt)</a>
<button class="btn" onclick="window.print()">Print / save as PDF</button>
<form method="post" action="{url_for('reports.queue')}" style="display:inline"><button class="btn primary">Queue for medical team</button></form>
<p class="small" style="margin-top:.6rem">Queued reports are saved to the <code>outbox</code> folder and sent on the next automatic sync, or uploaded manually.</p></div>"""
    return layout("Weekly report", body, "weekly", th)

def report_text():
    cur, now, before, rad, flags, low, tips = build_week()
    if not cur:
        return "ZENAUT WEEKLY REPORT\n" + "=" * 50 + "\nNo readings in the last 7 days.\n"
    ov = now["overall"]
    L = [f"ZENAUT WEEKLY REPORT  {cur[0]['day']} to {cur[-1]['day']}", "=" * 50,
         f"Overall {fmt(ov, '{:.0f}')}/100" + (f" ({LABEL[status(ov)]})" if ov is not None else "") + f"  ({len(cur)} day(s) with data)",
         f"Mental {fmt(now['mental'], '{:.0f}')} | Physical {fmt(now['physical'], '{:.0f}')}",
         f"Mood {fmt(now['mood'], '{:.1f}')}/10 | Stress {fmt(now['stress'], '{:.0f}')}/100 | Sleep {fmt(now['sleep_hours'], '{:.1f}')} h",
         f"Hydration {fmt(now['hydration_l'], '{:.1f}')} L | HR {fmt(now['heart_rate'], '{:.0f}')} | SpO2 {fmt(now['spo2'], '{:.0f}')}% | Radiation {fmt(rad, '{:.2f}')} mSv",
         "", "ALERTS"] + ([f" - {d}: {f}" for d, f in flags] or [" - none"])
    if low: L.append(" - Danger-zone days: " + ", ".join(low))
    L += ["", "RECOMMENDATIONS"] + [f" - {t}" for t in tips] + ["", "Estimates for wellbeing tracking; not a medical diagnosis."]
    return "\n".join(L)

@reports.route("/weekly.txt")
def weekly_txt():
    sync_now()
    seed_demo()
    return Response(report_text(), mimetype="text/plain",
                    headers={"Content-Disposition": "attachment; filename=zenaut_weekly_report.txt"})

@reports.route("/weekly/queue", methods=["POST"])
def queue():
    sync_now()
    seed_demo()
    OUTBOX.mkdir(exist_ok=True)
    f = OUTBOX / f"weekly_{date.today().isoformat()}.txt"
    f.write_text(report_text(), encoding="utf-8")
    body = f'<div class="card"><h2>Queued</h2><p>Saved to <code>{escape(str(f.name))}</code> in the outbox. It will go out on the next sync.</p></div>'
    return layout("Weekly report", body, "weekly", theme_of())
