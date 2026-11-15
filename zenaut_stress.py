"""ZENAUT · Mission Stress Check, inside Your wellbeing (Flask blueprint).

Your questions, scoring and bands are unchanged. Results are saved on this device in
stress_data.json (works offline). Page: /wellbeing/stress/   API: /wellbeing/stress/api
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from html import escape

from flask import Blueprint, jsonify, request, url_for

from zenaut_reports import layout, line_chart

stress = Blueprint("stress", __name__, url_prefix="/wellbeing/stress")
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "stress_data.json")

QUESTIONS = [
    ["Mission overload", "I felt overwhelmed by everything on my plate."],
    ["Can’t power down", "I had trouble relaxing, even with free time."],
    ["Static on the line", "I felt irritable or on edge."],
    ["Sleep cycle disrupted", "My mind wouldn’t slow down at night."],
    ["Pressure in the cabin", "I felt tense in my body (jaw, shoulders, stomach)."],
    ["Navigation fog", "I found it hard to concentrate or make decisions."],
    ["Off course", "I felt I couldn’t control the important things in my life."],
    ["Ground control", "I felt I had enough support when things got hard."],
]
REVERSED = {7}  # "Ground control" is reverse scored (4 = never, 0 = very often)
SCALE = ["Not at all", "A little", "Sometimes", "Quite a bit", "Very much"]  # 0 to 4, about today


def load_history():
    try:
        with open(DATA_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def save_history(history):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


def score_answers(answers):
    """Return the per-question scores (reverse scoring applied) or None if invalid."""
    if not isinstance(answers, list) or len(answers) != len(QUESTIONS):
        return None
    if not all(isinstance(a, int) and 0 <= a <= 4 for a in answers):
        return None
    return [4 - a if i in REVERSED else a for i, a in enumerate(answers)]


def band(total):
    if total <= 10:
        return "low"
    if total <= 20:
        return "mid"
    return "high"


def day_of(entry):
    """Calendar day of a check-in (older entries without a day use their UTC date)."""
    return entry.get("day") or entry["ts"][:10]


def streak(history):
    """Number of days in a row with a check-in (today not yet done does not break it)."""
    days = {day_of(e) for e in history}
    cur = date.today()
    if cur.isoformat() not in days:
        cur -= timedelta(days=1)
    n = 0
    while cur.isoformat() in days:
        n += 1
        cur -= timedelta(days=1)
    return n


# band -> (colour used across Your wellbeing, label, advice)
BAND = {
    "low": ("green", "Low stress", "Stress looks manageable. Keep your routine, sleep and contact with home."),
    "mid": ("orange", "Some strain", "You are carrying some strain. Try a grounding exercise today and talk to someone you trust."),
    "high": ("red", "High strain", "Your stress is high. Please contact your behavioural health support or flight surgeon today."),
}

CSS = """
.wb .qrow{padding:.9rem 0;border-bottom:1px solid rgba(174,178,177,.2)}
.wb .qrow:last-of-type{border-bottom:0}
.wb .qrow b{display:block}
.wb .scale{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}
.wb .opt{position:relative;margin:0;font-size:inherit;color:inherit;display:block}
.wb .opt input{position:absolute;opacity:0;width:auto;margin:0;padding:0;border:0}
.wb .opt span{display:block;padding:.4rem .8rem;border:1px solid var(--grey);border-radius:999px;cursor:pointer;font-size:.85rem;color:var(--text)}
.wb .opt input:checked+span{background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
.wb .opt input:focus-visible+span{outline:3px solid var(--text);outline-offset:2px}
.wb .qrow.miss{border-left:4px solid var(--warn);padding-left:.7rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
"""

JS = """
(function(){
  var btn=document.getElementById("qsend"),msg=document.getElementById("qmsg");
  btn.onclick=function(){
    var a=[],first=null,n=%d;
    for(var i=0;i<n;i++){
      var c=document.querySelector('input[name="q'+i+'"]:checked'),row=document.getElementById("qr"+i);
      if(c){a.push(parseInt(c.value,10));row.classList.remove("miss")}
      else{row.classList.add("miss");if(!first)first=row}
    }
    if(first){msg.textContent="Please answer every question.";first.scrollIntoView({block:"center"});return}
    btn.disabled=true;msg.textContent="Saving...";
    fetch("%s",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({answers:a})})
      .then(function(r){if(!r.ok)throw 0;location.reload()})
      .catch(function(){btn.disabled=false;msg.textContent="Could not save. Try again."});
  };
  var clr=document.getElementById("qclear");
  if(clr)clr.onclick=function(){
    if(confirm("Delete all saved stress check-ins on this device?"))
      fetch("%s",{method:"DELETE"}).then(function(){location.reload()});
  };
})();
"""


def latest_card(history):
    if not history:
        return '<div class="card"><h2>Latest result</h2><p class="small">No check-ins yet. Answer the questions below.</p></div>'
    e = history[-1]
    colour, label, advice = BAND[e["band"]]
    pressure = [QUESTIONS[i][0] for i, s in enumerate(e["scores"]) if s >= 3]
    pts = f'<p class="small" style="margin-top:.6rem">Biggest pressure points: {escape(", ".join(pressure))}.</p>' if pressure else ""
    helper = (f'<p style="margin-top:.6rem"><a class="btn" href="{url_for("module", slug="grounding")}">Open grounding exercises</a></p>'
              if e["band"] != "low" else "")
    return (f'<div class="card"><h2>Latest result</h2>'
            f'<div class="stat">{e["total"]}<span class="small"> /32</span> <span class="badge {colour}">{label}</span></div>'
            f'<div class="meter"><i class="{colour}" style="width:{e["total"] / 32 * 100:.0f}%"></i></div>'
            f'<div class="alert {colour}">{advice}</div>{pts}{helper}'
            f'<p class="small" style="margin-top:.6rem">{day_of(e)} {e.get("time", "")}. Lower is better. This is a check-in, not a diagnosis.</p></div>')


def daily_card(history):
    today = date.today().isoformat()
    done = next((e for e in history if day_of(e) == today), None)
    n = streak(history)
    streak_txt = f'<p class="small" style="margin-top:.5rem">Check-in streak: {n} day{"" if n == 1 else "s"}.</p>'
    if done:
        status = f'<div class="alert green"><b>Done for today.</b> Checked in at {escape(done.get("time", ""))}. You can update it below if things change.</div>'
    else:
        status = '<div class="alert orange"><b>Today’s check-in is not done yet.</b> It takes about a minute.</div>'
    return f'<div class="card"><h2>Daily check-in</h2>{status}{streak_txt}</div>', bool(done)


@stress.route("/")
def page():
    history = load_history()
    daily_html, done_today = daily_card(history)
    qs = ""
    for i, (title, text) in enumerate(QUESTIONS):
        opts = "".join(f'<label class="opt"><input type="radio" name="q{i}" value="{v}"><span>{escape(s)}</span></label>'
                       for v, s in enumerate(SCALE))
        qs += f'<div class="qrow" id="qr{i}"><b>{escape(title)}</b><span class="small">{escape(text)}</span><div class="scale">{opts}</div></div>'
    chart = ""
    if history:
        recent = history[-30:]
        chart = ('<div class="card"><h2>Stress over time</h2>'
                 + line_chart([day_of(e) for e in recent], [("Total (lower is better)", [e["total"] for e in recent], 0)],
                              ymin=0, ymax=32, zones=False)
                 + '<div class="row"><button class="btn" id="qclear" type="button">Clear history</button></div></div>')
    body = (f'<style>{CSS}</style>{daily_html}{latest_card(history)}'
            f'<div class="card"><h2>{"Update today’s check-in" if done_today else "Today’s check-in"}</h2><p class="small">Today, how much did each of these apply to you?</p>'
            f'{qs}<div class="row"><button class="btn primary" id="qsend" type="button">{"Update check-in" if done_today else "Save check-in"}</button><span class="small" id="qmsg"></span></div></div>'
            f'{chart}<script>{JS % (len(QUESTIONS), url_for("stress.add_checkin"), url_for("stress.clear_history"))}</script>')
    return layout("Stress check", body, "stress")


@stress.route("/api", methods=["GET"])
def get_history():
    return jsonify(load_history())


@stress.route("/api", methods=["POST"])
def add_checkin():
    payload = request.get_json(silent=True) or {}
    scores = score_answers(payload.get("answers"))
    if scores is None:
        return jsonify(error="Send 8 answers, each a whole number from 0 to 4."), 400
    entry = {"ts": datetime.now(timezone.utc).isoformat(), "day": date.today().isoformat(),
             "time": datetime.now().strftime("%H:%M"), "scores": scores, "total": sum(scores)}
    entry["band"] = band(entry["total"])
    # one check-in per day: a new one today replaces the earlier one from today
    history = [e for e in load_history() if day_of(e) != entry["day"]]
    history.append(entry)
    save_history(history[-365:])  # keep up to a year of daily check-ins
    return jsonify(entry), 201


@stress.route("/api", methods=["DELETE"])
def clear_history():
    save_history([])
    return jsonify(ok=True)
