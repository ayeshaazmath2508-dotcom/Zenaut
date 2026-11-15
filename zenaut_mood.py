"""ZENAUT · Mood Monitor, its own page on the dashboard (Flask blueprint).

A short daily check-in: four questions answered with emojis (1 to 5), plus a safety box.
Sleep and journaling have their own sections, so they are not part of this page.
Data is saved on this device in mood_log.json (works offline). Pages live under /mood/.
Prototype for education. Not a medical device.
"""
import json
import random
from datetime import date, timedelta
from html import escape
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS, line_chart

mood = Blueprint("mood", __name__, url_prefix="/mood")
LOG_FILE = Path(__file__).with_name("mood_log.json")

# key, question, five answers from 1 to 5 as (emoji, caption)
QUESTIONS = [
    ("mood", "How is your mood?", [("😞", "Very low"), ("🙁", "Low"), ("😐", "Okay"), ("🙂", "Good"), ("😄", "Great")]),
    ("energy", "How is your energy?", [("😴", "Drained"), ("🥱", "Tired"), ("😐", "Okay"), ("🙂", "Good"), ("⚡", "Energised")]),
    ("stress", "How stressed do you feel?", [("😌", "Calm"), ("🙂", "A little"), ("😐", "Some"), ("😟", "A lot"), ("😫", "Extreme")]),
    ("anxiety", "How anxious do you feel?", [("😌", "Calm"), ("🙂", "A little"), ("😐", "Some"), ("😟", "A lot"), ("😰", "Extreme")]),
]
EMOJI = {k: [e for e, _ in opts] for k, _, opts in QUESTIONS}
MOOD_LABEL = {i + 1: c for i, (_, c) in enumerate(QUESTIONS[0][2])}
STATUS_LABEL = {"green": "SAFE", "orange": "MONITORING", "red": "DANGER"}
STATUS_VAR = {"green": "var(--ok)", "orange": "var(--warn)", "red": "var(--bad)"}

CSS = """
.wb .subnav{margin:.6rem 0 0}.wb .subnav a{font-size:.75rem;padding:.3rem .8rem}
.wb .mtile{background:var(--panel);border:1px solid rgba(174,178,177,.25);border-left:6px solid var(--grey);border-radius:14px;padding:.8rem 1rem}
.wb .mtile .lbl{color:var(--grey);font-size:.8rem}
.wb .mtile .val{font-size:1.8rem;font-weight:700;line-height:1.2}
.wb .mtile .sub{color:var(--grey);font-size:.82rem}
.wb .strip{display:flex;gap:6px;margin:1rem 0 .4rem}
.wb .dchip{flex:1;text-align:center;border-radius:10px;padding:.4rem 0;font-weight:700;color:#07102A;font-size:.82rem}
.wb .dchip small{display:block;font-weight:600;opacity:.8}
.wb .q{padding:1rem 0;border-bottom:1px solid rgba(174,178,177,.2)}
.wb .q b{display:block;font-size:1.05rem}
.wb .escale{display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.6rem}
.wb .eopt{position:relative;margin:0;font-size:inherit;color:inherit;display:block;cursor:pointer}
.wb .eopt input{position:absolute;opacity:0;width:auto;margin:0;padding:0;border:0}
.wb .eopt span{display:flex;flex-direction:column;align-items:center;gap:.15rem;min-width:78px;padding:.55rem .5rem;border:2px solid rgba(174,178,177,.4);border-radius:14px;background:var(--space);color:var(--text);font-size:.78rem}
.wb .eopt span i{font-style:normal;font-size:2rem;line-height:1.1}
.wb .eopt:hover span{border-color:var(--grey)}
.wb .eopt input:checked+span{border-color:var(--red);background:var(--panel2);font-weight:700}
.wb .eopt input:focus-visible+span{outline:3px solid var(--text);outline-offset:2px}
.wb .qmiss{border-left:4px solid var(--warn);padding-left:.7rem}
.wb .chk{display:flex;gap:.5rem;align-items:flex-start;margin-top:1rem;font-size:.9rem;color:var(--text)}
.wb .chk input{width:auto;margin-top:.25rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
"""

# ------------------------------------------------------------------ data helpers
def assess(e):
    """Wellness score 1-5 (higher is better) and a green / orange / red status."""
    score = round((e["mood"] + e["energy"] + (6 - e["stress"]) + (6 - e["anxiety"])) / 4, 2)
    if e.get("unsafe") or score < 2.0 or (e["mood"] == 1 and e["stress"] == 5):
        return score, "red"
    return score, ("orange" if score < 3.5 else "green")

def load_log():
    try:
        data = json.loads(LOG_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    for e in data:  # older entries (with sleep) are re-scored with the current formula
        e["score"], e["status"] = assess(e)
    return sorted(data, key=lambda e: e["date"])

def save_log(log):
    LOG_FILE.write_text(json.dumps(log, indent=2), encoding="utf-8")

def D(e):
    return date.fromisoformat(e["date"])

def mean(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0

def clip(v, lo=1, hi=5):
    return max(lo, min(hi, v))

def emo(key, v):
    return EMOJI[key][int(v) - 1]

def sample_entries(days=14):
    rnd = random.Random(7)
    out = []
    for i in range(days - 1, -1, -1):
        m = clip(round(rnd.gauss(3.6, 0.9)))
        stress = clip(round(2.6 - (m - 3.6) * 0.6 + rnd.gauss(0, 0.5)))
        e = {"date": str(date.today() - timedelta(days=i)), "mood": m, "energy": clip(round(m + rnd.gauss(0, 0.6))),
             "stress": stress, "anxiety": clip(round(stress - 0.5 + rnd.gauss(0, 0.6))), "unsafe": False, "sample": True}
        e["score"], e["status"] = assess(e)
        out.append(e)
    return out

def week_slice(log, end=None):
    end = end or D(log[-1])
    return [e for e in log if end - timedelta(days=6) <= D(e) <= end]

def streak(log):
    days = {D(e) for e in log}
    d, n = D(log[-1]), 0
    while d in days:
        n += 1
        d -= timedelta(days=1)
    return n

def insights(log):
    out = []
    end = D(log[-1])
    wk, prev = week_slice(log, end), week_slice(log, end - timedelta(days=7))
    if len(wk) >= 3 and len(prev) >= 3:
        d = mean(e["mood"] for e in wk) - mean(e["mood"] for e in prev)
        out.append(f"Mood is {'up' if d > 0 else 'down'} {abs(d):.1f} points compared with the previous week."
                   if abs(d) >= 0.3 else "Mood is steady compared with the previous week.")
        s = mean(e["stress"] for e in wk) - mean(e["stress"] for e in prev)
        if abs(s) >= 0.5:
            out.append(f"Stress is {'higher' if s > 0 else 'lower'} than the previous week ({abs(s):.1f} points).")
    if len(wk) >= 3 and mean(e["stress"] for e in wk) >= 4:
        out.append("Stress has been high most days this week. Try a grounding exercise and talk to someone you trust.")
    if len(log) >= 7:
        by = {}
        for e in log:
            by.setdefault(D(e).weekday(), []).append(e["mood"])
        names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        out.append(f"Your best days on average are {names[max(by, key=lambda k: mean(by[k]))]}s.")
    return out or ["Log a few more days to unlock insights."]

def flags(log):
    out = []
    if log[-1]["status"] == "red":
        out.append(("red", "Your latest check-in is in the danger range. Contact your flight surgeon or ground team."))
    tail = log[-3:]
    if len(tail) == 3:
        ms = [e["mood"] for e in tail]
        if ms[0] >= ms[1] >= ms[2] and ms[0] > ms[2]:
            out.append(("orange", "Mood has dropped across your last three check-ins."))
        if mean(e["stress"] for e in tail) >= 4:
            out.append(("orange", "Stress has stayed high for three check-ins."))
    return out

def recommend(e):
    if e["status"] == "red":
        return "Contact your flight surgeon or ground team now. Stay with a crewmate and breathe slowly until help responds."
    tips = []
    if e["stress"] >= 4 or e["anxiety"] >= 4:
        tips.append("box breathing in Grounding and exercises")
    if e["mood"] <= 2:
        tips.append("a call or message to someone you love")
    return "Suggested: " + "; ".join(tips or ["keep your routine: exercise, meals and time with your crew"]) + "."

# ------------------------------------------------------------------ ui helpers
def badge(status):
    return f'<span class="badge {status}">{STATUS_LABEL[status]}</span>'

def tile(label, value, sub="", color="var(--grey)"):
    return (f'<div class="mtile" style="border-left-color:{color}"><div class="lbl">{label}</div>'
            f'<div class="val">{value}</div><div class="sub">{sub}</div></div>')

def page(title, body, active):
    items = [("dashboard", "Dashboard", "mood.dashboard"), ("log", "Log today", "mood.log_page"),
             ("weekly", "Weekly graphs", "mood.weekly")]
    sub = "".join(f'<a class="{"on" if k == active else ""}" href="{url_for(e)}">{t}</a>' for k, t, e in items)
    body = f'<style>{CSS}</style><nav class="subnav">{sub}</nav><h2 style="margin-top:1.2rem">{title}</h2>{body}'
    return render_template("wellbeing.html", title="Daily emoji check-in and trends", links="", body=Markup(body),
                           css=Markup(WB_CSS), heading="Mood Monitor", active="mood")

def empty_state():
    return ('<div class="card"><p>No check-ins yet. Log today, or load 14 days of sample data to explore this page.</p>'
            f'<div class="row"><a class="btn primary" href="{url_for("mood.log_page")}">Log today</a>'
            f'<form method="post" action="{url_for("mood.load_sample")}" style="display:inline"><button class="btn">Load sample data</button></form></div></div>')

# ------------------------------------------------------------------ pages
@mood.route("/")
def dashboard():
    log = load_log()
    if not log:
        return page("Dashboard", empty_state(), "dashboard")
    last, wk = log[-1], week_slice(log)
    prev = week_slice(log, D(last) - timedelta(days=7))
    out = f'<div class="card">{badge(last["status"])} &nbsp; Latest check-in: {last["date"]}'
    if D(last) < date.today():
        out += f'<p class="small">You have not logged today yet. <a href="{url_for("mood.log_page")}" style="text-decoration:underline">Log today</a>.</p>'
    out += "".join(f'<div class="alert {lv}">{escape(t)}</div>' for lv, t in flags(log)) + "</div>"
    good = lambda v: "var(--ok)" if v >= 4 else "var(--warn)" if v == 3 else "var(--bad)"
    bad = lambda v: "var(--ok)" if v <= 2 else "var(--warn)" if v == 3 else "var(--bad)"
    out += ('<div class="grid" style="margin-top:1rem">'
            + tile("Mood", f'{emo("mood", last["mood"])} {last["mood"]}/5', MOOD_LABEL[last["mood"]], good(last["mood"]))
            + tile("Energy", f'{emo("energy", last["energy"])} {last["energy"]}/5', "higher is better", good(last["energy"]))
            + tile("Stress", f'{emo("stress", last["stress"])} {last["stress"]}/5', "lower is better", bad(last["stress"]))
            + tile("Anxiety", f'{emo("anxiety", last["anxiety"])} {last["anxiety"]}/5', "lower is better", bad(last["anxiety"])) + "</div>")
    d = mean(e["mood"] for e in wk) - mean(e["mood"] for e in prev) if prev else None
    out += ('<h2 style="margin-top:1.2rem">This week</h2><div class="grid">'
            + tile("Avg mood", f'{mean(e["mood"] for e in wk):.1f}', f"{d:+.1f} vs last week" if d is not None else "no prior week yet")
            + tile("Avg stress", f'{mean(e["stress"] for e in wk):.1f}', "lower is better")
            + tile("Wellness score", f'{mean(e["score"] for e in wk):.1f}', "out of 5")
            + tile("Streak", f"{streak(log)} d", "days logged in a row") + "</div>")
    by_day = {D(e): e for e in log}
    chips = ""
    for i in range(6, -1, -1):
        day = D(last) - timedelta(days=i)
        col = STATUS_VAR[by_day[day]["status"]] if day in by_day else "rgba(174,178,177,.3)"
        chips += f'<div class="dchip" style="background:{col}">{day.strftime("%a")}<small>{day.day}</small></div>'
    out += f'<div class="strip">{chips}</div>'
    out += ('<div class="card"><h2>Mood and energy</h2>' + line_chart([e["date"] for e in wk], [("Mood", [e["mood"] for e in wk], 0),
            ("Energy", [e["energy"] for e in wk], 1)], ymin=1, ymax=5, zones=False) + '</div>')
    out += ('<div class="card"><h2>Insights</h2><ul class="clean">' + "".join(f"<li>{escape(t)}</li>" for t in insights(log))
            + f'</ul><p class="small" style="margin-top:.6rem">{escape(recommend(last))}</p></div>')
    return page("Dashboard", out, "dashboard")

FORM_JS = """<script>
document.getElementById("mm").onsubmit=function(e){
  var first=null;
  document.querySelectorAll(".q").forEach(function(q){
    var ok=q.querySelector("input:checked");q.classList.toggle("qmiss",!ok);if(!ok&&!first)first=q});
  if(first){e.preventDefault();document.getElementById("mmsg").textContent="Please answer every question.";first.scrollIntoView({block:"center"})}
};
</script>"""

@mood.route("/log")
def log_page():
    today = date.today().isoformat()
    qs = ""
    for key, text, opts in QUESTIONS:
        buttons = "".join(f'<label class="eopt"><input type="radio" name="{key}" value="{i}"><span><i>{em}</i>{escape(cap)}</span></label>'
                          for i, (em, cap) in enumerate(opts, 1))
        qs += f'<div class="q"><b>{escape(text)}</b><div class="escale">{buttons}</div></div>'
    form = (f'<form method="post" id="mm" action="{url_for("mood.save_entry")}" class="card">'
            f'<label for="date">Date</label><input type="date" id="date" name="date" value="{today}" max="{today}" required style="width:auto">'
            f'{qs}'
            '<label class="chk"><input type="checkbox" name="unsafe" value="1"><span>I feel unsafe, have thoughts of harming myself, or have a serious physical symptom</span></label>'
            '<div class="row"><button class="btn primary">Save check-in</button><span class="small" id="mmsg"></span></div>'
            '<p class="small">Saving again for the same date replaces that day’s check-in.</p></form>' + FORM_JS)
    return page("Log today", form, "log")

def parse_entry(f):
    try:
        d = date.fromisoformat(f.get("date", ""))
        if d > date.today():
            raise ValueError
        vals = {k: int(f.get(k, "")) for k, _, _ in QUESTIONS}
        if not all(1 <= v <= 5 for v in vals.values()):
            raise ValueError
    except ValueError:
        return None
    e = {"date": str(d), **vals, "unsafe": f.get("unsafe") == "1", "sample": False}
    e["score"], e["status"] = assess(e)
    return e

@mood.route("/log", methods=["POST"])
def save_entry():
    e = parse_entry(request.form)
    if e is None:
        return page("Log today", '<div class="alert red">Some answers were missing or not valid. Go back and check the form.</div>', "log"), 400
    save_log(sorted([x for x in load_log() if x["date"] != e["date"]] + [e], key=lambda x: x["date"]))
    out = f'<div class="card">{badge(e["status"])} &nbsp; Wellness score {e["score"]} / 5'
    if e["status"] == "red":
        out += ('<div class="alert red">Tell a crewmate and contact your flight surgeon or ground team now. You are not alone. '
                'This prototype cannot send the alert itself.</div>')
    out += f'<p style="margin-top:.6rem">{escape(recommend(e))}</p>'
    if e["status"] != "green":
        out += f'<p style="margin-top:.6rem"><a class="btn" href="{url_for("module", slug="grounding")}">Open grounding exercises</a></p>'
    out += f'<p class="small" style="margin-top:.6rem">Saved for {e["date"]}.</p><a class="btn primary" href="{url_for("mood.dashboard")}">Back to dashboard</a></div>'
    return page("Check-in saved", out, "log")

@mood.route("/sample", methods=["POST"])
def load_sample():
    save_log(sorted(load_log() + sample_entries(), key=lambda x: x["date"]))
    return redirect(url_for("mood.dashboard"))

@mood.route("/weekly")
def weekly():
    log = load_log()
    if not log:
        return page("Weekly graphs", empty_state(), "weekly")
    ends = sorted({e["date"] for e in log}, reverse=True)
    end = request.args.get("end") if request.args.get("end") in ends else ends[0]
    wk = week_slice(log, date.fromisoformat(end))
    opts = "".join(f'<option value="{d}"{" selected" if d == end else ""}>{d}</option>' for d in ends)
    best = max(wk, key=lambda e: e["mood"])
    cell = lambda k, e: f'<td>{emo(k, e[k])} {e[k]}</td>'
    rows = "".join(f'<tr><td>{e["date"]}</td>{cell("mood", e)}{cell("energy", e)}{cell("stress", e)}{cell("anxiety", e)}'
                   f'<td>{e["score"]}</td><td>{badge(e["status"])}</td></tr>' for e in wk)
    out = (f'<form method="get" class="row"><label for="end">Week ending</label><select id="end" name="end" style="width:auto" onchange="this.form.submit()">{opts}</select></form>'
           f'<p class="small">{wk[0]["date"]} to {wk[-1]["date"]} | {len(wk)} check-ins</p><div class="grid">'
           + tile("Mood", f'{mean(e["mood"] for e in wk):.1f}', "weekly average") + tile("Energy", f'{mean(e["energy"] for e in wk):.1f}', "weekly average")
           + tile("Stress", f'{mean(e["stress"] for e in wk):.1f}', "weekly average")
           + tile("Best day", D(best).strftime("%a"), f'mood {best["mood"]}') + '</div>'
           '<div class="card"><h2>Mood, energy, stress and anxiety</h2>'
           + line_chart([e["date"] for e in wk], [("Mood", [e["mood"] for e in wk], 0), ("Energy", [e["energy"] for e in wk], 1),
                                                   ("Stress", [e["stress"] for e in wk], 2), ("Anxiety", [e["anxiety"] for e in wk], 3)],
                        ymin=1, ymax=5, zones=False)
           + '</div><div class="card"><h2>Daily table</h2><div style="overflow-x:auto"><table><tr><th>Date</th><th>Mood</th><th>Energy</th>'
           f'<th>Stress</th><th>Anxiety</th><th>Score</th><th>Status</th></tr>{rows}</table></div></div>')
    return page("Weekly graphs", out, "weekly")
