"""ZENAUT · Sleep and circadian centre (Flask blueprint, works offline).

Lives at /sleepcare and replaces the old single Sleep page (the Sleep tile and /sleep now open it).
Tabs:
  Overview            today's plan, last night, "I can't sleep" button, schedule settings
  Circadian calculator questions -> chronotype, social jet lag, body-clock estimates, recommendations
  Sleep monitor       nightly log, mission time vs Earth time, 14-night chart, sleep debt, regularity
  Light & solar       smart daylight schedule (CCT + lux), live lamp preview, predicted solar exposure
  Wind-down & relax   personalised wind-down timeline, breathing and relaxation routines
  Reminders           wind-down / avoid-bright-light / avoid-blue-light notifications (+ auto warm screen tint)

Data is saved on this device in sleep_data.json (plus wake time and environment in astrocare_data.json).
Nightly sleep is also copied into "Your wellbeing" (sleep hours and sleep score).
Not a medical device. The questionnaires are simple screening aids, not validated clinical instruments.
"""
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone
from html import escape
from pathlib import Path

from flask import Blueprint, jsonify, redirect, render_template, request, url_for
from markupsafe import Markup

import zenaut_astrocare as ac
from zenaut_reports import CSS as WB_CSS, db as reports_db, line_chart, save_day, FIELDS

sleepcare = Blueprint("sleepcare", __name__, url_prefix="/sleepcare")
DATA = Path(__file__).with_name("sleep_data.json")

TABS = [("overview", "Overview"), ("circadian", "Circadian calculator"), ("monitor", "Sleep monitor"),
        ("light", "Light & solar"), ("winddown", "Wind-down & relax"), ("reminders", "Reminders")]

# ------------------------------------------------------------------ storage
def load():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("answers", {})
    d.setdefault("result", None)
    d.setdefault("log", [])
    d.setdefault("notify", {})
    d.setdefault("earth_offset", 0.0)      # Earth (home) clock minus mission clock, in hours
    d.setdefault("moon_lon", 0.0)
    d.setdefault("mars_lat", 18.4)
    d.setdefault("mars_ls", 90)
    d.setdefault("dust", False)
    d.setdefault("rover_body", "Mars")
    return d

def save(d):
    DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")

# ------------------------------------------------------------------ small helpers
def hm(m):
    m = int(round(m)) % 1440
    return f"{m // 60:02d}:{m % 60:02d}"

def tm(s, default=None):
    try:
        t = datetime.strptime((s or "").strip(), "%H:%M")
        return t.hour * 60 + t.minute
    except ValueError:
        return default

def sdiff(a, b):
    """Signed shortest difference a-b on a 24 h clock, in minutes."""
    return (a - b + 720) % 1440 - 720

def dur_txt(minutes):
    minutes = int(round(minutes))
    return f"{minutes // 60} h {minutes % 60:02d} min"

def clamp(x, lo, hi):
    return max(lo, min(hi, x))

def to_int(v, default=0, lo=None, hi=None):
    try:
        x = int(float(v))
    except (TypeError, ValueError):
        x = default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x

def to_float(v, default=0.0, lo=None, hi=None):
    try:
        x = float(v)
    except (TypeError, ValueError):
        x = default
    if lo is not None:
        x = max(lo, x)
    if hi is not None:
        x = min(hi, x)
    return x

def cct_rgb(k):
    """Approximate lamp colour for a colour temperature in kelvin (for the preview swatches)."""
    t = k / 100.0
    if t <= 66:
        r = 255
        g = 99.4708025861 * math.log(t) - 161.1195681661
        b = 0 if t <= 19 else 138.5177312231 * math.log(t - 10) - 305.0447927307
    else:
        r = 329.698727446 * (t - 60) ** -0.1332047592
        g = 288.1221695283 * (t - 60) ** -0.0755148492
        b = 255
    return "#%02X%02X%02X" % tuple(int(clamp(c, 0, 255)) for c in (r, g, b))

# ------------------------------------------------------------------ questions
PREF = [
    ("pref_wake", "If you were free to choose, when would you naturally wake up?",
     ["Before 06:00", "06:00 to 07:30", "07:30 to 09:00", "After 09:00"]),
    ("alert", "When in the day do you feel sharpest?",
     ["Early morning", "Late morning to midday", "Afternoon", "Evening or night"]),
    ("hardwake", "How hard is it to get up at a fixed early time?",
     ["Very easy", "Fairly easy", "Fairly hard", "Very hard"]),
    ("groggy", "In the first 30 minutes after waking, how alert are you?",
     ["Fully alert", "Fairly alert", "Groggy", "Very groggy"]),
    ("evening", "In the hour before bed, how do you feel?",
     ["Very sleepy", "Getting sleepy", "Still fairly awake", "Wide awake and energetic"]),
    ("bedpref", "With no obligations, when would you naturally go to bed?",
     ["Before 22:00", "22:00 to 23:30", "23:30 to 01:00", "After 01:00"]),
]
# key, question, options best habit first (score 3,2,1,0), advice key used when habit is weak (option 3rd/4th)
HABITS = [
    ("morning_light", "Bright light (viewport, bright lamp or tunable LED at full) in the first 2 hours after waking?",
     ["60 min or more", "30 to 60 min", "10 to 30 min", "Under 10 min"]),
    ("evening_light", "In the last hour before bed, how much bright light or screen time do you have?",
     ["Dim light, no screens", "Dim light, a little screen use", "Screens for most of the hour", "Bright lights and screens"]),
    ("caffeine", "When is your last caffeine (coffee, tea, energy drinks)?",
     ["Before 12:00", "12:00 to 15:00", "15:00 to 18:00", "After 18:00"]),
    ("naps", "Do you nap?",
     ["No, or a short nap before 15:00", "Sometimes 30 to 90 min", "Often", "Often after 17:00"]),
    ("exercise", "Exercise timing",
     ["Regular, finished 3+ h before bed", "Regular, sometimes close to bed", "Rarely", "Hard exercise close to bed"]),
    ("meals", "Meals and snacks",
     ["Regular times, nothing heavy in the last 3 h", "Mostly regular", "Irregular", "Heavy meals or snacking close to bed"]),
    ("room", "Your sleep area",
     ["Dark, quiet, comfortable", "Mostly dark and quiet", "Some light or noise", "Bright or noisy"]),
]
SQ = [
    ("sq_fall", "Trouble falling asleep"),
    ("sq_wake", "Waking in the night and struggling to get back to sleep"),
    ("sq_early", "Waking much earlier than planned"),
    ("sq_day", "Daytime sleepiness or poor concentration"),
]
SQ_EXTRA = [
    ("sq_worry", "Racing thoughts or worry at bedtime"),
    ("sq_tense", "Body tension or restlessness at bedtime"),
]
SQ_SCALE = ["Not at all", "A little", "Somewhat", "Much", "Very much"]
CHRONO = ["Definite morning type", "Moderate morning type", "Intermediate type", "Moderate evening type", "Definite evening type"]
CHRONO_NOTE = [
    "Your body clock runs early. You do best with early wake-ups and bright mornings; guard against late evenings.",
    "Your body clock leans early. Morning light and a steady bedtime will keep it there.",
    "Your body clock is in the middle, which makes shifting the schedule a little easier.",
    "Your body clock leans late. Strong morning light and dim evenings help pull it earlier.",
    "Your body clock runs late. Early duty is hard for you: use bright light right after waking and strict evening dimming.",
]

DEFAULT_ANSWERS = {"duty_bed": "22:30", "duty_lat": 15, "duty_wake": "06:30",
                   "free_bed": "23:30", "free_lat": 15, "free_wake": "07:30", "duty_days": 5}

def chrono_index(msfsc):
    h = msfsc / 60.0
    return 0 if h < 2.5 else 1 if h < 3.5 else 2 if h < 5 else 3 if h < 6 else 4

def analyse(form):
    """Turn the questionnaire answers into the circadian result (all numbers in minutes unless noted)."""
    a = {}
    a["duty_bed"] = hm(tm(form.get("duty_bed"), 22 * 60 + 30)); a["duty_wake"] = hm(tm(form.get("duty_wake"), 6 * 60 + 30))
    a["free_bed"] = hm(tm(form.get("free_bed"), 23 * 60 + 30)); a["free_wake"] = hm(tm(form.get("free_wake"), 7 * 60 + 30))
    a["duty_lat"] = to_int(form.get("duty_lat"), 15, 0, 180); a["free_lat"] = to_int(form.get("free_lat"), 15, 0, 180)
    a["duty_days"] = to_int(form.get("duty_days"), 5, 0, 7)
    for key, _, opts in PREF + HABITS:
        a[key] = to_int(form.get(key), 1, 0, len(opts) - 1)
    for key, _ in SQ + SQ_EXTRA:
        a[key] = to_int(form.get(key), 0, 0, 4)

    def block(bed, lat, wake):
        onset = (tm(bed) + lat) % 1440
        dur = (tm(wake) - onset) % 1440
        return onset, dur, (onset + dur / 2.0) % 1440

    on_w, sdw, mid_w = block(a["duty_bed"], a["duty_lat"], a["duty_wake"])
    on_f, sdf, mid_f = block(a["free_bed"], a["free_lat"], a["free_wake"])
    n = a["duty_days"]
    sd_week = (n * sdw + (7 - n) * sdf) / 7.0
    norm = lambda m: ((m + 720) % 1440) - 720          # so 23:00 is -1 h, not 23 h
    msf, msw = norm(mid_f), norm(mid_w)
    msfsc = msf - 0.5 * (sdf - sd_week) if sdf > sd_week else msf    # sleep-debt corrected (MCTQ)
    sjl = abs(msf - msw)
    pref_score = sum(4 - a[k] for k, _, _ in PREF)
    pref_pct = round((pref_score - 6) / 18 * 100)
    ci = chrono_index(msfsc)
    pref_ci = 0 if pref_pct >= 85 else 1 if pref_pct >= 65 else 2 if pref_pct >= 45 else 3 if pref_pct >= 25 else 4
    habit_score = sum(3 - a[k] for k, _, _ in HABITS)
    flags = [k for k, _, _ in HABITS if a[k] >= 2]
    sq_total = sum(a[k] for k, _ in SQ)
    r = dict(a=a, sdw=round(sdw), sdf=round(sdf), msw=round(msw), msf=round(msf), msfsc=round(msfsc),
             sjl=round(sjl), chrono_idx=ci, pref_pct=pref_pct, pref_idx=pref_ci,
             habit_score=habit_score, habit_pct=round(habit_score / 21 * 100), flags=flags,
             sq_total=sq_total, sq_worry=a["sq_worry"], sq_tense=a["sq_tense"],
             onset_free=round(on_f), wake_free=tm(a["free_wake"]), lat=a["free_lat"],
             when=datetime.now().strftime("%Y-%m-%d %H:%M"))
    return r

def sq_band(total):
    if total <= 4:
        return "green", "Low concern"
    if total <= 8:
        return "orange", "Mild: keep watching"
    if total <= 12:
        return "orange", "Moderate: act on the advice below"
    return "red", "High: speak to your flight surgeon"

# ------------------------------------------------------------------ plan: bedtime, events, light
def current_plan(d=None, ad=None):
    d = d or load()
    ad = ad or ac.load()
    wake = tm(ad["wake"], 7 * 60)
    res = d["result"]
    need = clamp(res["sdf"] / 60.0, 7, 9) if res else 8.0
    lat = res["lat"] if res else 0
    bed = int(round(wake - need * 60 - lat)) % 1440
    return dict(wake=wake, bed=bed, need=need, env=ad["env"], res=res)

def day_events(plan):
    wake, bed, res = plan["wake"], plan["bed"], plan["res"]
    flags = res["flags"] if res else []
    sq = res["sq_total"] if res else 0
    dim = 240 if sq >= 9 else 180
    screen_note = " Your answers show late screen use, so be strict about this one." if "evening_light" in flags else ""
    caf_note = " Your answers show late caffeine, so this matters for you." if "caffeine" in flags else ""
    ev = [
        dict(key="wake_light", at=wake, title="Wake-up light",
             msg="Get bright, cool light now (500+ lux, about 6500 K) for 30 to 60 minutes. It anchors your body clock.", kind="light"),
        dict(key="caffeine", at=bed - 480, title="Last caffeine",
             msg="Caffeine can still be active 8 hours later. Switch to water or decaf from here." + caf_note, kind="habit"),
        dict(key="dim", at=bed - dim, title="Start dimming the lights",
             msg=f"About {dim // 60} hours to bed. Switch to warm, dim light (about 2700 K, under 100 lux) and avoid bright light.", kind="light"),
        dict(key="blue", at=bed - 120, title="Avoid blue light",
             msg="Screens to night mode and lowest brightness, or put them away. Blue-rich light delays melatonin." + screen_note, kind="light"),
        dict(key="unwind", at=bed - 75, title="Time to unwind",
             msg="Start your wind-down: hygiene, gentle stretching, something quiet. Leave tasks and messages for tomorrow.", kind="winddown"),
        dict(key="relax", at=bed - 30, title="Relaxation exercise",
             msg="Do a short relaxation: 4-7-8 breathing or a body scan (Wind-down & relax tab). Lights amber and very low.", kind="winddown"),
        dict(key="lights_out", at=bed, title="Lights out",
             msg="Time for sleep. Lights off, sleep mask on, white noise if you like. Use only red or amber light if you need to move.", kind="sleep"),
    ]
    for e in ev:
        e["at"] %= 1440
        e["time"] = hm(e["at"])
    return ev

def light_blocks(plan):
    wake, bed, res = plan["wake"], plan["bed"], plan["res"]
    awake = (bed - wake) % 1440
    late = bool(res) and res["chrono_idx"] >= 3
    sq = res["sq_total"] if res else 0
    boost = 90 if late else 60
    dim = 240 if sq >= 9 else 180
    e3 = max(360, awake - dim)
    spec = [
        ("Wake boost", 0, boost, 6500, (500, 1000), "Strongest light of the day. Sets and anchors the body clock."),
        ("Focus light", boost, 360, 5500, (400, 500), "Bright and cool-neutral for alertness and mood."),
        ("Steady daylight", 360, e3, 4200, (250, 300), "Comfortable working light. Keep at least 250 lux at the eyes."),
        ("Warm dim", e3, awake - 60, 2700, (30, 100), "Lower the level and warm the colour. Little blue light."),
        ("Amber wind-down", awake - 60, awake, 2200, (5, 30), "Very dim amber. No bright screens."),
        ("Sleep", awake, 1440, 1800, (0, 1), "Off. If you must see, use dim red or amber (under 5 lux, away from the eyes)."),
    ]
    out = []
    for name, s, e, k, (lo, hi), note in spec:
        out.append(dict(name=name, start=(wake + s) % 1440, end=(wake + e) % 1440, dur=e - s, cct=k,
                        lo=lo, hi=hi, hex=cct_rgb(k), note=note))
    return out

def bright_hours(blocks):
    return sum(b["dur"] for b in blocks if b["hi"] >= 250) / 60.0

def recommendations(plan, res):
    """List of (level, title, text) built from the circadian answers and the current plan."""
    out = []
    wake, bed = plan["wake"], plan["bed"]
    if not res:
        out.append(("orange", "Take the circadian calculator",
                    "Answer the questions on the Circadian calculator tab to personalise this plan to your body clock."))
        return out
    f = res["flags"]
    if "morning_light" in f:
        out.append(("orange", "Get bright light early",
                    f"Spend 30 to 60 minutes in bright, cool light (500+ lux, 6500 K) from {hm(wake)}. Use the viewport or your tunable LED on full."))
    if "evening_light" in f:
        out.append(("orange", "Cut evening light",
                    f"From {hm(bed - 180)} use warm dim light, and from {hm(bed - 120)} use night mode or no screens. Blue-rich light delays your body clock."))
    if "caffeine" in f:
        out.append(("orange", "Move your last caffeine earlier", f"Stop caffeine by {hm(bed - 480)} (8 hours before bed)."))
    if "naps" in f:
        out.append(("orange", "Tame the naps", "Keep naps under 30 minutes and finish them before 15:00 so they do not steal sleep drive."))
    if "exercise" in f:
        out.append(("orange", "Time your exercise", f"Finish hard exercise by {hm(bed - 180)}. Morning and late afternoon workouts help sleep most."))
    if "meals" in f:
        out.append(("orange", "Steady your meals", f"Eat at regular times and finish large meals by {hm(bed - 180)}."))
    if "room" in f:
        out.append(("orange", "Improve the sleep area", "Use a sleep mask, earplugs or white noise (White noise page), and block LEDs. Keep it cool and comfortable."))
    if res["sjl"] >= 60:
        out.append(("orange", "Reduce social jet lag",
                    f"Your sleep midpoint differs by {dur_txt(res['sjl'])} between duty and free days. Keep your wake time within 30 minutes every day."))
    elif res["sjl"] >= 30:
        out.append(("green", "Mild social jet lag", f"{dur_txt(res['sjl'])} between duty and free days. Fine, but keep wake times steady."))
    if res["chrono_idx"] >= 3:
        out.append(("green", "Late body clock: how to move earlier",
                    "Bright light right after waking, dim warm evenings, no late caffeine, and shift bedtime 15 to 20 minutes earlier every few days."))
    if res["chrono_idx"] <= 1:
        out.append(("green", "Early body clock: protect it", "Avoid bright light late in the evening so you do not drift later, and keep morning light strong."))
    if plan["env"] == "Mars surface":
        out.append(("orange", "Mars sol schedule",
                    "A sol is 24 h 39 min, about 40 minutes longer than an Earth day. Either lock to the 24 h clock with scheduled lighting, or move your wake and bed time about 40 minutes later each Earth day (Light & solar tab shows the 7-day schedule)."))
    if plan["env"] == "Space station (LEO)":
        out.append(("green", "16 sunrises a day",
                    "Do not follow the window light. Close shutters or use a mask at night and let the cabin lighting schedule set your day."))
    band, label = sq_band(res["sq_total"])
    if band != "green":
        out.append((band, "Sleep quality: " + label.lower(),
                    "If trouble sleeping lasts more than 2 weeks or you feel very low or anxious, tell your flight surgeon or behavioural health team."))
    if not out or all(o[0] == "green" for o in out):
        out.insert(0, ("green", "Good habits", "Your answers show few sleep-timing problems. Keep your wake time, morning light and evening dimming steady."))
    return out

def phase_estimates(res):
    """Rough clock estimates from the free-day timing (typical patterns, not a measurement)."""
    wk, on = res["wake_free"], res["onset_free"]
    return dict(dlmo=(on - 150) % 1440, cbtmin=(wk - 150) % 1440,
                focus=((wk + 150) % 1440, (wk + 300) % 1440), dip=((wk + 420) % 1440, (wk + 540) % 1440),
                peak=((wk + 600) % 1440, (wk + 720) % 1440), hard=((on - 180) % 1440, (on - 60) % 1440))

# ------------------------------------------------------------------ solar exposure prediction
LUNAR = 29.530588853
NEW_MOON_REF = datetime(2000, 1, 6, 18, 14)
SEASONS = {0: "Northern spring (Ls 0)", 90: "Northern summer (Ls 90)", 180: "Northern autumn (Ls 180)", 270: "Northern winter (Ls 270)"}

def strip_svg(cells, label_every, label_fn, marker=None):
    """A row of cells whose brightness shows how much light there is (0 to 1)."""
    n, W, L = len(cells), 620, 6
    cw = (W - 2 * L) / n
    s = [f'<svg class="chart" viewBox="0 0 {W} 62" role="img">']
    for i, v in enumerate(cells):
        s.append(f'<rect x="{L + i * cw:.1f}" y="6" width="{cw - 1.5:.1f}" height="26" rx="3" style="fill:var(--warn)" opacity="{0.12 + 0.88 * v:.2f}"/>')
    for i in range(0, n, label_every):
        s.append(f'<text x="{L + i * cw + cw / 2:.1f}" y="50" text-anchor="middle">{escape(label_fn(i))}</text>')
    if marker is not None:
        x = L + marker * cw + cw / 2
        s.append(f'<path d="M{x - 5:.1f} 40 L{x + 5:.1f} 40 L{x:.1f} 33 Z" style="fill:var(--text)"/>')
    s.append("</svg>")
    return "".join(s)

def mars_daylight(lat, ls):
    eps = math.radians(25.19)
    dec = math.asin(math.sin(eps) * math.sin(math.radians(ls)))
    x = -math.tan(math.radians(lat)) * math.tan(dec)
    if x >= 1:
        return 0.0
    if x <= -1:
        return 1.0
    return math.acos(x) / math.pi

def solar_card(plan, d, blocks):
    env = plan["env"]
    body = ac.ENVIRONMENTS[env]["sun"]
    if env == "Rover (surface)":
        env_body = d["rover_body"]
    else:
        env_body = {"Mars surface": "Mars", "Moon surface": "Moon"}.get(env)
    out = [f'<div class="card"><h2>Predicted solar exposure · {escape(env)}</h2>']
    if env == "Space station (LEO)":
        orbit, lit = 92.9, 0.65
        cells = [1.0] * 20 + [0.0] * 11
        out.append(strip_svg(cells, 5, lambda i: f"{i * 3} min"))
        out.append(f'<p>One orbit is about {orbit:.0f} minutes: roughly {orbit * lit:.0f} minutes of sunlight, then {orbit * (1 - lit):.0f} minutes of darkness. '
                   f'That is about {1440 / orbit:.1f} sunrises every 24 hours and about {24 * lit:.0f} hours of sunlight in total.</p>'
                   '<p class="small">Sunlit share changes with the orbit angle to the Sun (the beta angle), so treat these as typical values. '
                   'Window light is the wrong signal for your body clock: use shutters and let scheduled cabin light set your day.</p>')
    elif env == "Spacecraft (transit)":
        cells = []
        for h in range(24):
            m = h * 60 + 30
            b = next((x for x in blocks if (m - x["start"]) % 1440 < x["dur"]), blocks[-1])
            cells.append(min(1.0, b["hi"] / 500.0))
        out.append(strip_svg(cells, 3, lambda i: f"{i:02d}h"))
        out.append(f'<p>No natural day and night in transit, so your light dose is whatever the cabin lighting delivers. '
                   f'Your plan gives about {bright_hours(blocks):.1f} hours at 250 lux or more, then dim, warm light before sleep.</p>'
                   f'<p class="small">{escape(body)}</p>')
    elif env_body == "Moon":
        age = (datetime.now(timezone.utc).replace(tzinfo=None) - NEW_MOON_REF).total_seconds() / 86400.0 % LUNAR
        rise = (LUNAR / 4 - d["moon_lon"] / 360.0 * LUNAR) % LUNAR
        sset = (rise + LUNAR / 2) % LUNAR
        lit = lambda a: ((a - rise) % LUNAR) < LUNAR / 2
        cells = [1.0 if lit(i + 0.5) else 0.0 for i in range(30)]
        up = lit(age)
        to_next = ((sset if up else rise) - age) % LUNAR
        when = datetime.now() + timedelta(days=to_next)
        out.append(strip_svg(cells, 5, lambda i: f"day {i}", marker=int(age)))
        state = "in sunlight" if up else "in lunar night"
        nxt = "sunset" if up else "sunrise"
        out.append(f'<p>At longitude {d["moon_lon"]:g}° the site is <b>{state}</b> now (lunar day {age:.1f} of {LUNAR:.1f}). '
                   f'Next {nxt} in about {to_next:.1f} Earth days (around {when.strftime("%d %b")}). Sunlight lasts about {LUNAR / 2:.1f} days, then about {LUNAR / 2:.1f} days of night.</p>'
                   '<p class="small">Simple model for a site near the equator: sunrise follows first quarter at the centre of the near side. '
                   'Polar sites and crater floors differ a lot. Use an artificial 24 h light cycle regardless of the Sun.</p>')
    elif env_body == "Mars":
        lat, ls = d["mars_lat"], d["mars_ls"]
        frac = mars_daylight(lat, ls)
        dust = 0.4 if d["dust"] else 1.0
        cells = []
        for i in range(24):
            lo, hi = i / 24.0, (i + 1) / 24.0
            a, b = 0.5 - frac / 2, 0.5 + frac / 2
            cells.append(max(0.0, min(hi, b) - max(lo, a)) * 24 * dust)
        sol_h = 24.6597
        out.append(strip_svg(cells, 3, lambda i: f"{i:02d}h"))
        out.append(f'<p>At {lat:g}°N in {escape(SEASONS[ls].lower())}, daylight lasts about <b>{frac * sol_h:.1f} hours</b> of each sol (24 h 39 min). '
                   + ('A dust storm is switched on: daylight is dimmed to a fraction of normal. ' if d["dust"] else '')
                   + 'Mars gets roughly 40 to 45 percent of the sunlight Earth does at the top of the atmosphere, and dust can cut it much more.</p>'
                   f'<p class="small">Bars show sunlight through the sol (sol-hours, noon in the middle). Dust storm season is roughly Ls 180 to 330. '
                   'Clocks drift about 40 minutes a day against Earth: see the drift table below.</p>')
    else:
        out.append(f'<p>{escape(body)}</p>')
    out.append("</div>")
    return "".join(out)

# ------------------------------------------------------------------ sleep monitor logic
def need_minutes(plan):
    return plan["need"] * 60

def night(entry_in, need):
    bed, wake = tm(entry_in["bed"]), tm(entry_in["wake"])
    dur = (wake - bed) % 1440
    aw = to_int(entry_in.get("aw"), 0, 0, 9)
    q = to_int(entry_in.get("q"), 3, 1, 5)
    score = round(clamp(60 * min(dur / need, 1.0) + 7 * (q - 1) + (12 - 4 * min(aw, 3)), 0, 100))
    return dict(date=entry_in["date"], bed=hm(bed), wake=hm(wake), dur=dur, aw=aw, q=q, note=entry_in.get("note", "")[:120],
                mid=round((bed + dur / 2.0) % 1440), score=score)

def push_wellbeing(day, hours, score):
    """Copy the night into 'Your wellbeing' without wiping that day's other readings."""
    try:
        with reports_db() as con:
            row = con.execute("SELECT * FROM daily WHERE day=?", (day,)).fetchone()
        data = {}
        if row is not None and not row["demo"]:
            data = {f: row[f] for f in FIELDS}
        data["sleep_hours"], data["sleep_score"] = round(hours, 1), score
        save_day(data, day=day, demo=0)
    except Exception:
        pass

def sd(values):
    return statistics.pstdev(values) if len(values) > 1 else 0.0

def monitor_stats(log, need):
    last = log[-7:]
    if not last:
        return None
    avg = sum(e["dur"] for e in last) / len(last)
    debt = sum(max(0, need - e["dur"]) for e in last)
    ref = last[0]["mid"]
    reg_mid = sd([sdiff(e["mid"], ref) for e in last])
    refb = tm(last[0]["bed"])
    reg_bed = sd([sdiff(tm(e["bed"]), refb) for e in last])
    return dict(avg=avg, debt=debt, reg_mid=reg_mid, reg_bed=reg_bed, score=sum(e["score"] for e in last) / len(last), n=len(last))

def actogram(log, plan, offset_h):
    rows = log[-14:]
    if not rows:
        return ""
    W, L, R, T, RH = 640, 64, 10, 16, 18
    H = T + RH * len(rows) + 26
    X = lambda m: L + m / 1440.0 * (W - L - R)
    s = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-label="Sleep timing, last {len(rows)} nights">']
    for h in range(0, 25, 3):
        x = X(h * 60)
        s.append(f'<line class="axis" x1="{x:.1f}" x2="{x:.1f}" y1="{T - 4}" y2="{H - 22}"/><text x="{x:.1f}" y="{H - 8}" text-anchor="middle">{h:02d}</text>')
    wake, bed = plan["wake"], plan["bed"]
    segs = [(0, wake), (bed, 1440)] if bed > wake else [(bed, wake)]
    for i, e in enumerate(rows):
        y = T + i * RH
        for a, b in segs:
            s.append(f'<rect x="{X(a):.1f}" y="{y}" width="{X(b) - X(a):.1f}" height="{RH - 4}" rx="3" fill="#FFFFFF" opacity=".07"/>')
        bd, wk = tm(e["bed"]), tm(e["wake"])
        parts = [(bd, wk)] if bd < wk else [(0, wk), (bd, 1440)]
        for a, b in parts:
            s.append(f'<rect x="{X(a):.1f}" y="{y}" width="{max(X(b) - X(a), 2):.1f}" height="{RH - 4}" rx="3" style="fill:var(--c0)">'
                     f'<title>{e["date"]}: {e["bed"]} to {e["wake"]} ({dur_txt(e["dur"])})</title></rect>')
        s.append(f'<text x="{L - 6}" y="{y + RH - 7}" text-anchor="end">{e["date"][5:]}</text>')
    if offset_h:
        x = X((-offset_h * 60) % 1440)
        s.append(f'<line x1="{x:.1f}" x2="{x:.1f}" y1="{T - 4}" y2="{H - 22}" stroke="#FC3D21" stroke-dasharray="4 3" stroke-width="1.5"/>'
                 f'<text x="{x:.1f}" y="{T - 6}" text-anchor="middle" style="fill:#FC3D21">Earth 00:00</text>')
    s.append("</svg>")
    return "".join(s) + ('<div class="legend"><span><i style="background:var(--c0)"></i>Sleep logged</span>'
                         '<span><i style="background:#8a8f9c"></i>Planned sleep window</span></div>')

# ------------------------------------------------------------------ page shell + CSS
CSS = """
.wb .sl-grid{display:grid;gap:.8rem;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));margin-top:.6rem}
.wb .sl-stat{background:var(--panel2);border-radius:14px;padding:.8rem .9rem;border-left:5px solid var(--grey)}
.wb .sl-stat.green{border-color:var(--ok)}.wb .sl-stat.orange{border-color:var(--warn)}.wb .sl-stat.red{border-color:var(--bad)}
.wb .sl-stat b{display:block;font-size:1.35rem;line-height:1.25}.wb .sl-stat span{font-size:.78rem;color:var(--grey)}
.wb .qrow{border-bottom:1px solid rgba(174,178,177,.2);padding:.75rem 0}.wb .qrow:last-of-type{border:0}
.wb .qrow>b{display:block;font-size:.95rem}.wb .scale{display:flex;flex-wrap:wrap;gap:.4rem;margin-top:.45rem}
.wb label.opt{display:inline-flex;gap:.45rem;align-items:center;padding:.35rem .8rem;border:1px solid var(--grey);border-radius:999px;
 font-size:.82rem;color:var(--text);cursor:pointer;margin:0}
.wb label.opt input{width:auto;margin:0;accent-color:var(--red)}
.wb label.opt:has(input:checked){background:var(--red);border-color:var(--red);color:#fff;font-weight:700}
.wb .tl{list-style:none;margin:0;padding:0}
.wb .tl li{display:grid;grid-template-columns:64px 1fr;gap:.8rem;padding:.6rem 0;border-bottom:1px solid rgba(174,178,177,.2)}
.wb .tl li:last-child{border:0}.wb .tl .t{font-weight:800;font-variant-numeric:tabular-nums}.wb .tl .on{color:var(--warn)}
.wb .sw{width:18px;height:18px;border-radius:50%;display:inline-block;vertical-align:middle;border:1px solid rgba(255,255,255,.35);margin-right:.4rem}
.wb .lamp{position:relative;height:130px;border-radius:16px;background:#0b0f1f;overflow:hidden;margin:.6rem 0}
.wb .lamp i{position:absolute;inset:0;transition:background .3s,opacity .3s}
.wb .lamp span{position:absolute;inset:0;display:grid;place-items:center;text-align:center;font-weight:800;color:#fff;text-shadow:0 1px 6px #000;padding:.5rem}
.wb input[type=range]{padding:0;border:0;background:transparent;accent-color:var(--red)}
.wb .row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-top:.7rem}
.wb .two{display:grid;gap:1rem;grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}
.wb .two .card{margin-top:1rem}
.wb .pill{display:inline-block;padding:.1rem .6rem;border-radius:999px;border:1px solid var(--grey);font-size:.75rem;margin:0 .3rem .3rem 0}
.wb .pill.hl{background:var(--warn);border-color:var(--warn);color:#07102A;font-weight:700}
.wb .switch{display:flex;gap:.6rem;align-items:center;font-size:.95rem;color:var(--text)}
.wb .switch input{width:auto;margin:0;accent-color:var(--red);transform:scale(1.3)}
.wb .btn.sm{padding:.25rem .7rem}
.wb .danger{color:var(--bad)}
.wb .sosbtn{font-size:1rem;padding:.7rem 1.6rem}
"""

def page(heading, body, active):
    links = "".join(f'<a class="{"on" if k == active else ""}" href="{url_for("sleepcare." + k)}">{t}</a>' for k, t in TABS)
    return render_template("wellbeing.html", title="Sleep & circadian centre", heading=heading, links=Markup(links),
                           body=Markup(f"<style>{CSS}</style>{body}"), css=Markup(WB_CSS), active="sleep")

def stat(label, value, level=""):
    return f'<div class="sl-stat {level}"><span>{escape(label)}</span><b>{escape(value)}</b></div>'

def recs_html(recs):
    return "".join(f'<div class="alert {lv}"><b>{escape(t)}.</b> {escape(x)}</div>' for lv, t, x in recs)

def env_options(sel):
    return "".join(f'<option{" selected" if e == sel else ""}>{escape(e)}</option>' for e in ac.ENVIRONMENTS)

def now_advice(plan):
    n = datetime.now()
    m = n.hour * 60 + n.minute
    wake, bed = plan["wake"], plan["bed"]
    if (m - bed) % 1440 < (wake - bed) % 1440:
        return ac.lighting_advice(0, -1)
    return ac.lighting_advice((m - wake) % 1440, (bed - m) % 1440)

def next_event(events):
    n = datetime.now()
    m = n.hour * 60 + n.minute
    return min(events, key=lambda e: (e["at"] - m) % 1440)

# ------------------------------------------------------------------ Overview
@sleepcare.route("/")
def overview():
    d, ad = load(), ac.load()
    plan = current_plan(d, ad)
    events = day_events(plan)
    res = d["result"]
    nxt = next_event(events)
    log = sorted(d["log"], key=lambda e: e["date"])
    ms = monitor_stats(log, need_minutes(plan))
    settings = (f'<div class="card"><h2>Your schedule</h2><form method="post" action="{url_for("sleepcare.settings")}" class="log">'
                '<input type="hidden" name="next" value="overview">'
                f'<label>Wake-up time<input type="time" name="wake" value="{escape(ad["wake"])}" required></label>'
                f'<label>Environment<select name="env">{env_options(ad["env"])}</select></label>'
                '<div style="align-self:end"><button class="btn primary">Update plan</button></div></form>'
                f'<p class="small" style="margin-top:.6rem">Bedtime is worked out from your wake time and sleep need'
                + (" (from your calculator answers)." if res else ". Take the Circadian calculator to personalise it.") + '</p></div>')
    tonight = (f'<div class="card"><h2>Tonight</h2><div class="sl-grid">'
               + stat("Lights out", hm(plan["bed"])) + stat("Wake up", hm(plan["wake"])) + stat("Sleep target", dur_txt(plan["need"] * 60))
               + stat("Next reminder", f'{nxt["time"]} · {nxt["title"]}') + '</div>'
               f'<div class="alert orange" style="margin-top:.8rem"><b>Right now.</b> {escape(now_advice(plan))}</div></div>')
    if ms:
        last = log[-1]
        lvl = "green" if ms["avg"] >= plan["need"] * 60 - 30 else "orange" if ms["avg"] >= 360 else "red"
        night_card = ('<div class="card"><h2>Sleep monitor</h2><div class="sl-grid">'
                      + stat("Last night", f'{dur_txt(last["dur"])} · {last["score"]}/100', "green" if last["dur"] >= need_minutes(plan) - 30 else "orange")
                      + stat(f'{ms["n"]}-night average', dur_txt(ms["avg"]), lvl)
                      + stat("Sleep debt (7 nights)", dur_txt(ms["debt"]), "green" if ms["debt"] < 120 else "orange" if ms["debt"] < 300 else "red")
                      + stat("Timing regularity", f'±{ms["reg_mid"]:.0f} min', "green" if ms["reg_mid"] < 30 else "orange")
                      + f'</div><p style="margin-top:.8rem"><a class="btn" href="{url_for("sleepcare.monitor")}">Open sleep monitor</a></p></div>')
    else:
        night_card = (f'<div class="card"><h2>Sleep monitor</h2><p>No nights logged yet. Log last night to see duration, sleep debt and regularity.</p>'
                      f'<p><a class="btn primary" href="{url_for("sleepcare.monitor")}">Log a night</a></p></div>')
    cant_n = sum(1 for t in ad["cant_sleep"] if t >= (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S"))
    cant = ('<div class="card"><h2>Trouble falling asleep?</h2><p>Press the button for guided breathing and relaxation exercises that help you drift off.</p>'
            f'<form method="post" action="{url_for("astro.cant_sleep")}"><button class="btn primary sosbtn">I can\'t sleep</button></form>'
            + (f'<p class="small" style="margin-top:.5rem">You pressed this {cant_n} time{"s" if cant_n != 1 else ""} in the last 7 days.'
               + (" If it keeps happening, tell your flight surgeon." if cant_n >= 3 else "") + "</p>" if cant_n else "") + '</div>')
    recs = recommendations(plan, res)[:4]
    recs_card = f'<div class="card"><h2>Recommendations for you</h2>{recs_html(recs)}</div>'
    tiles = "".join(f'<a class="btn" href="{url_for("sleepcare." + k)}" style="margin:.2rem">{t}</a>' for k, t in TABS[1:])
    body = settings + tonight + night_card + recs_card + cant + f'<div class="card"><h2>Jump to</h2>{tiles}</div>'
    return page("Sleep & circadian centre", body, "overview")

@sleepcare.route("/settings", methods=["POST"])
def settings():
    d, ad, f = load(), ac.load(), request.form
    w = tm(f.get("wake"))
    if w is not None:
        ad["wake"] = hm(w)
    if f.get("env") in ac.ENVIRONMENTS:
        ad["env"] = f["env"]
    ac.save(ad)
    if "earth_offset" in f:
        d["earth_offset"] = to_float(f["earth_offset"], 0.0, -14, 14)
    if "moon_lon" in f:
        d["moon_lon"] = to_float(f["moon_lon"], 0.0, -180, 180)
    if "mars_lat" in f:
        d["mars_lat"] = to_float(f["mars_lat"], 18.4, -90, 90)
    if to_int(f.get("mars_ls"), -1) in SEASONS:
        d["mars_ls"] = to_int(f["mars_ls"])
    if "site_form" in f:
        d["dust"] = "dust" in f
    if f.get("rover_body") in ("Mars", "Moon"):
        d["rover_body"] = f["rover_body"]
    save(d)
    nxt = f.get("next", "overview")
    return redirect(url_for("sleepcare." + (nxt if nxt in dict(TABS) else "overview")))

# ------------------------------------------------------------------ Circadian calculator
def opt_rows(items, answers, scale_fn=None):
    html = ""
    for key, text, opts in items:
        radios = "".join(f'<label class="opt"><input type="radio" name="{key}" value="{i}"{" checked" if answers.get(key) == i else ""} required><span>{escape(o)}</span></label>'
                         for i, o in enumerate(opts))
        html += f'<div class="qrow"><b>{escape(text)}</b><div class="scale">{radios}</div></div>'
    return html

def result_html(plan, res):
    ci = res["chrono_idx"]
    est = phase_estimates(res)
    agree = "agrees with" if abs(res["pref_idx"] - ci) <= 1 else "differs from"
    band, blabel = sq_band(res["sq_total"])
    sjl_lvl = "green" if res["sjl"] < 30 else "orange" if res["sjl"] < 90 else "red"
    stats = ('<div class="sl-grid">' + stat("Chronotype", CHRONO[ci])
             + stat("Mid-sleep, corrected", hm(res["msfsc"]))
             + stat("Social jet lag", dur_txt(res["sjl"]), sjl_lvl)
             + stat("Sleep need (free days)", dur_txt(res["sdf"]))
             + stat("Habit score", f'{res["habit_pct"]} / 100', "green" if res["habit_pct"] >= 70 else "orange")
             + stat("Sleep problems", blabel, band) + "</div>")
    rng = lambda t: f"{hm(t[0])} to {hm(t[1])}"
    table = ("<table>"
             f'<tr><th>Melatonin starts rising (estimate)</th><td>{hm(est["dlmo"])}</td></tr>'
             f'<tr><th>Body-temperature low (deepest sleep drive)</th><td>{hm(est["cbtmin"])}</td></tr>'
             f'<tr><th>Best focus window</th><td>{rng(est["focus"])}</td></tr>'
             f'<tr><th>Afternoon dip (keep tasks easy, short light break)</th><td>{rng(est["dip"])}</td></tr>'
             f'<tr><th>Second peak, good for exercise and social time</th><td>{rng(est["peak"])}</td></tr>'
             f'<tr><th>Hardest time to fall asleep early</th><td>{rng(est["hard"])}</td></tr></table>')
    gap = sdiff(plan["wake"], res["wake_free"])
    gaptxt = ("Your planned wake time matches your natural rhythm." if abs(gap) <= 30 else
              f"Your planned wake time is {dur_txt(abs(gap))} {'earlier' if gap < 0 else 'later'} than your natural free-day wake time. "
              + ("Shift in steps of 15 to 20 minutes every few days and use morning light to help." if gap < 0 else "Keep morning light strong to avoid drifting later."))
    return (f'<div class="card"><h2>Your circadian profile</h2>{stats}'
            f'<p style="margin-top:.8rem"><b>{escape(CHRONO[ci])}.</b> {escape(CHRONO_NOTE[ci])}</p>'
            f'<p class="small">Your preference answers ({res["pref_pct"]}% morningness) {agree} your sleep timing.</p></div>'
            f'<div class="card"><h2>Your body-clock estimates</h2>{table}<p class="small" style="margin-top:.5rem">Typical patterns worked out from your free-day sleep. '
            f'Individual timing varies by an hour or more. {escape(gaptxt)}</p></div>'
            f'<div class="card"><h2>Recommendations</h2>{recs_html(recommendations(plan, res))}</div>')

@sleepcare.route("/circadian", methods=["GET", "POST"])
def circadian():
    d = load()
    if request.method == "POST":
        res = analyse(request.form)
        d["result"], d["answers"] = res, res["a"]
        save(d)
        return redirect(url_for("sleepcare.circadian") + "#result")
    plan = current_plan(d)
    a = {**DEFAULT_ANSWERS, **d["answers"]}
    res = d["result"]
    top = ""
    if res:
        top = (f'<div id="result"><p class="small">Last calculated {escape(res["when"])}. Retake the questions below any time (for example after a few weeks).</p>'
               + result_html(plan, res) + "</div>")
    t = lambda name, label: f'<label>{label}<input type="time" name="{name}" value="{escape(str(a[name]))}" required></label>'
    n = lambda name, label, lo, hi: f'<label>{label}<input type="number" name="{name}" value="{a[name]}" min="{lo}" max="{hi}" required></label>'
    timing = ('<div class="card"><h2>1. Your sleep timing</h2><p class="small">Think about a typical week. Free days are days without alarms or duty. '
              'Sleep latency is how long you take to fall asleep.</p><h3 style="margin:.8rem 0 .2rem;font-size:.9rem">Duty days (with an alarm)</h3>'
              '<div class="log">' + t("duty_bed", "Went to bed") + n("duty_lat", "Minutes to fall asleep", 0, 180) + t("duty_wake", "Woke up") + '</div>'
              '<h3 style="margin:1rem 0 .2rem;font-size:.9rem">Free days (no alarm)</h3>'
              '<div class="log">' + t("free_bed", "Went to bed") + n("free_lat", "Minutes to fall asleep", 0, 180) + t("free_wake", "Woke up") + '</div>'
              '<div class="log" style="margin-top:.8rem">' + n("duty_days", "Duty days per week", 0, 7) + '</div></div>')
    pref = f'<div class="card"><h2>2. Morning or evening person</h2>{opt_rows(PREF, a)}</div>'
    hab = f'<div class="card"><h2>3. Light and daily habits</h2>{opt_rows(HABITS, a)}</div>'
    sq_rows = ""
    for key, text in SQ + SQ_EXTRA:
        radios = "".join(f'<label class="opt"><input type="radio" name="{key}" value="{i}"{" checked" if a.get(key, 0) == i else ""} required><span>{s}</span></label>'
                         for i, s in enumerate(SQ_SCALE))
        sq_rows += f'<div class="qrow"><b>{escape(text)}</b><div class="scale">{radios}</div></div>'
    sqc = f'<div class="card"><h2>4. Sleep quality, past 2 weeks</h2>{sq_rows}</div>'
    form = (f'<form method="post">{timing}{pref}{hab}{sqc}<div class="row"><button class="btn primary">Calculate my circadian rhythm</button></div></form>'
            '<p class="small" style="margin-top:.6rem">Timing maths follows the Munich Chronotype Questionnaire idea (mid-sleep on free days, corrected for sleep debt). '
            'The preference, habit and sleep-quality questions are simple screening aids made for this app, not validated clinical tests.</p>')
    return page("Circadian rhythm calculator", top + form, "circadian")

# ------------------------------------------------------------------ Sleep monitor
@sleepcare.route("/monitor")
def monitor():
    d, ad = load(), ac.load()
    plan = current_plan(d, ad)
    log = sorted(d["log"], key=lambda e: e["date"])
    off = d["earth_offset"]
    ms = monitor_stats(log, need_minutes(plan))
    today = date.today().isoformat()
    existing = next((e for e in log if e["date"] == today), None)
    ev = existing or {"bed": hm(plan["bed"]), "wake": hm(plan["wake"]), "aw": 0, "q": 3, "note": ""}
    qopts = "".join(f'<option value="{i}"{" selected" if ev["q"] == i else ""}>{i} · {w}</option>'
                    for i, w in zip(range(1, 6), ["Poor", "Fair", "OK", "Good", "Great"]))
    form = (f'<div class="card"><h2>Log a night</h2><form method="post" action="{url_for("sleepcare.log_night")}" class="log">'
            f'<label>Date you woke up<input type="date" name="date" value="{today}" max="{today}" required></label>'
            f'<label>Went to bed<input type="time" name="bed" value="{ev["bed"]}" required></label>'
            f'<label>Woke up<input type="time" name="wake" value="{ev["wake"]}" required></label>'
            f'<label>Times woken<input type="number" name="aw" min="0" max="9" value="{ev["aw"]}"></label>'
            f'<label>How rested?<select name="q">{qopts}</select></label>'
            f'<label>Note<input name="note" maxlength="120" value="{escape(ev.get("note", ""))}" placeholder="optional"></label>'
            '<div style="align-self:end"><button class="btn primary">Save night</button></div></form>'
            '<p class="small" style="margin-top:.5rem">Saving also updates sleep hours and sleep score in Your wellbeing. Logging the same date again replaces it.</p></div>')
    earth = (f'<div class="card"><h2>Mission time and Earth time</h2><form method="post" action="{url_for("sleepcare.settings")}" class="log">'
             '<input type="hidden" name="next" value="monitor">'
             f'<label>Earth (home) clock is ahead of mission clock by (hours)<input type="number" step="0.5" min="-14" max="14" name="earth_offset" value="{off:g}"></label>'
             '<div style="align-self:end"><button class="btn">Save</button></div></form>'
             '<p class="small" style="margin-top:.5rem">Every night is shown in mission time and in Earth time, so you can see how your sleep lines up with home. '
             'Use a negative number if home is behind your clock.</p>')
    if ad["env"] == "Mars surface":
        earth += (f'<div class="alert orange"><b>Mars sol.</b> Your day is 24 h 39 min, so your sleep drifts about 40 minutes later against Earth every day. '
                  'The chart shows that drift as a slanting pattern. See the drift table on the Light & solar tab.</div>')
    earth += "</div>"
    if not log:
        body = form + earth + '<div class="card"><p>No nights logged yet.</p></div>'
        return page("Sleep monitor", body, "monitor")
    nd = need_minutes(plan)
    stats = ('<div class="card"><h2>Last 7 nights</h2><div class="sl-grid">'
             + stat("Average sleep", dur_txt(ms["avg"]), "green" if ms["avg"] >= nd - 30 else "orange" if ms["avg"] >= 360 else "red")
             + stat("Sleep debt", dur_txt(ms["debt"]), "green" if ms["debt"] < 120 else "orange" if ms["debt"] < 300 else "red")
             + stat("Bedtime variation", f'±{ms["reg_bed"]:.0f} min', "green" if ms["reg_bed"] < 30 else "orange")
             + stat("Mid-sleep variation", f'±{ms["reg_mid"]:.0f} min', "green" if ms["reg_mid"] < 30 else "orange")
             + stat("Average sleep score", f'{ms["score"]:.0f} / 100', "green" if ms["score"] >= 75 else "orange" if ms["score"] >= 55 else "red")
             + f'</div><p class="small" style="margin-top:.6rem">Sleep need used: {dur_txt(nd)}. Aim for a bedtime that varies by less than 30 minutes.</p>')
    if ms["debt"] >= 300:
        stats += '<div class="alert red"><b>High sleep debt.</b> Protect tonight\'s sleep window, avoid caffeine after midday and tell your flight surgeon if this continues.</div>'
    elif ms["debt"] >= 120:
        stats += '<div class="alert orange"><b>Building sleep debt.</b> Go to bed on time and keep your wake time steady rather than sleeping in.</div>'
    stats += "</div>"
    chart = f'<div class="card"><h2>Sleep timing (mission clock)</h2>{actogram(log, plan, off)}</div>'
    dur_chart = ""
    if len(log) >= 2:
        r = log[-14:]
        dur_chart = ('<div class="card"><h2>Hours slept</h2>' + line_chart([e["date"] for e in r], [("Hours slept", [e["dur"] / 60 for e in r], 0)], ymin=0, ymax=12, zones=False) + '</div>')
    rows = ""
    for e in reversed(log[-14:]):
        bm, wm = tm(e["bed"]), tm(e["wake"])
        earth_txt = f'{hm(bm + off * 60)} to {hm(wm + off * 60)}'
        rows += (f'<tr><td>{e["date"][5:]}</td><td>{e["bed"]} to {e["wake"]}</td><td>{earth_txt}</td><td>{dur_txt(e["dur"])}</td>'
                 f'<td>{e["aw"]}</td><td>{e["q"]}/5</td><td>{e["score"]}</td>'
                 f'<td><form method="post" action="{url_for("sleepcare.delete_night")}"><input type="hidden" name="date" value="{e["date"]}">'
                 '<button class="btn sm" aria-label="Delete night">✕</button></form></td></tr>')
    table = ('<div class="card"><h2>Nights</h2><div style="overflow-x:auto"><table><tr><th>Date</th><th>Mission time</th><th>Earth time</th><th>Slept</th>'
             f'<th>Wake-ups</th><th>Rested</th><th>Score</th><th></th></tr>{rows}</table></div></div>')
    return page("Sleep monitor", form + stats + chart + dur_chart + earth + table, "monitor")

@sleepcare.route("/monitor/log", methods=["POST"])
def log_night():
    d, f = load(), request.form
    plan = current_plan(d)
    day = f.get("date", "")
    try:
        day = min(date.fromisoformat(day), date.today()).isoformat()
    except ValueError:
        day = date.today().isoformat()
    if tm(f.get("bed")) is None or tm(f.get("wake")) is None:
        return redirect(url_for("sleepcare.monitor"))
    e = night(dict(date=day, bed=f["bed"], wake=f["wake"], aw=f.get("aw"), q=f.get("q"), note=f.get("note", "")), need_minutes(plan))
    d["log"] = sorted([x for x in d["log"] if x["date"] != day] + [e], key=lambda x: x["date"])[-120:]
    save(d)
    push_wellbeing(day, e["dur"] / 60.0, e["score"])
    return redirect(url_for("sleepcare.monitor"))

@sleepcare.route("/monitor/delete", methods=["POST"])
def delete_night():
    d = load()
    d["log"] = [x for x in d["log"] if x["date"] != request.form.get("date")]
    save(d)
    return redirect(url_for("sleepcare.monitor"))

# ------------------------------------------------------------------ Light & solar
def site_form(d, env):
    inner = ""
    body = d["rover_body"] if env == "Rover (surface)" else {"Mars surface": "Mars", "Moon surface": "Moon"}.get(env)
    if env == "Rover (surface)":
        inner += ('<label>Rover is on<select name="rover_body">'
                  + "".join(f'<option{" selected" if b == d["rover_body"] else ""}>{b}</option>' for b in ("Mars", "Moon")) + "</select></label>")
    if body == "Moon":
        inner += f'<label>Site longitude (° east, near side)<input type="number" step="1" min="-180" max="180" name="moon_lon" value="{d["moon_lon"]:g}"></label>'
    if body == "Mars":
        seasons = "".join(f'<option value="{k}"{" selected" if k == d["mars_ls"] else ""}>{v}</option>' for k, v in SEASONS.items())
        inner += (f'<label>Latitude (° north, south is negative)<input type="number" step="0.1" min="-90" max="90" name="mars_lat" value="{d["mars_lat"]:g}"></label>'
                  f'<label>Season<select name="mars_ls">{seasons}</select></label>'
                  f'<label class="switch" style="align-self:end"><input type="checkbox" name="dust"{" checked" if d["dust"] else ""}>Dust storm</label>')
    if not inner:
        return ""
    return (f'<div class="card"><h2>Site settings</h2><form method="post" action="{url_for("sleepcare.settings")}" class="log">'
            f'<input type="hidden" name="next" value="light"><input type="hidden" name="site_form" value="1">{inner}'
            '<div style="align-self:end"><button class="btn primary">Update prediction</button></div></form></div>')

LAMP_JS = """
<script>
(function(){
  var B=__BLOCKS__,sl=document.getElementById("lslide"),lab=document.getElementById("ltime"),lamp=document.getElementById("lamp"),
      glow=lamp.querySelector("i"),txt=lamp.querySelector("span"),now=document.getElementById("lnow");
  function pad(n){return (n<10?"0":"")+n}
  function find(m){for(var i=0;i<B.length;i++){if((((m-B[i].start)%1440)+1440)%1440<B[i].dur)return B[i]}return B[B.length-1]}
  function show(m){
    var b=find(m);lab.textContent=pad(Math.floor(m/60))+":"+pad(m%60);
    var op=b.hi<=1?0.08:Math.min(1,0.25+0.75*Math.min(b.hi,500)/500);glow.style.background=b.hex;glow.style.opacity=op;
    txt.style.color=op>0.55?"#101828":"#fff";txt.style.textShadow=op>0.55?"none":"0 1px 6px #000";
    txt.innerHTML=b.name+"<br>"+b.cct+" K · "+(b.hi<=1?"off":b.lo+" to "+b.hi+" lux");
    document.querySelectorAll("#lsched tr[data-i]").forEach(function(r,i){r.className=B[i]===b?"on":""});
  }
  function nowMin(){var n=new Date();return n.getHours()*60+n.getMinutes()}
  sl.oninput=function(){show(+sl.value)};
  now.onclick=function(){sl.value=nowMin();show(nowMin())};
  sl.value=nowMin();show(nowMin());
})();
</script>"""

def lux_txt(b):
    return "off, under 5 lux amber" if b["hi"] <= 1 else "%s to %s lux" % (b["lo"], b["hi"])

@sleepcare.route("/light")
def light():
    d, ad = load(), ac.load()
    plan = current_plan(d, ad)
    blocks = light_blocks(plan)
    res = plan["res"]
    jb = [dict(name=b["name"], start=b["start"], dur=b["dur"], cct=b["cct"], lo=b["lo"], hi=b["hi"], hex=b["hex"]) for b in blocks]
    lamp = ('<div class="card"><h2>Smart daylight schedule</h2>'
            '<p class="small">Drag the slider to see how your lamp or tunable LED should look at any time of day. It follows your wake time and bedtime.</p>'
            '<div class="lamp" id="lamp"><i></i><span></span></div>'
            '<div class="row"><b id="ltime" style="font-variant-numeric:tabular-nums;min-width:3.5em">--:--</b>'
            '<input type="range" id="lslide" min="0" max="1439" step="5" style="flex:1;min-width:160px" aria-label="Time of day">'
            '<button class="btn sm" id="lnow" type="button">Now</button></div></div>')
    rows = "".join(f'<tr data-i="{i}"><td><span class="sw" style="background:{b["hex"]}"></span><b>{escape(b["name"])}</b></td>'
                   f'<td>{hm(b["start"])} to {hm(b["end"])}</td><td>{b["cct"]} K</td>'
                   f'<td>{lux_txt(b)}</td><td class="small">{escape(b["note"])}</td></tr>'
                   for i, b in enumerate(blocks))
    sched = (f'<div class="card"><h2>Recommended lighting</h2><div style="overflow-x:auto"><table id="lsched"><tr><th>Stage</th><th>When</th><th>Colour</th><th>Brightness</th><th>Why</th></tr>{rows}</table></div>'
             f'<p style="margin-top:.7rem"><a class="btn" href="{url_for("sleepcare.light_json")}">Download schedule for LED controller (JSON)</a></p>'
             '<p class="small" style="margin-top:.5rem">Targets follow published consensus guidance for healthy sleep: at least about 250 lux of daylight-like light at the eyes in the day, '
             'under about 10 lux (melanopic) in the 3 hours before bed, and darkness while asleep. Real fixtures differ, so calibrate with a lux meter if you have one.</p></div>')
    why = []
    if res and res["chrono_idx"] >= 3:
        why.append("Late body clock: the wake boost is extended to 90 minutes to help pull your rhythm earlier.")
    if res and res["sq_total"] >= 9:
        why.append("Sleep problems reported: dimming starts 4 hours before bed instead of 3.")
    if res and "morning_light" in res["flags"]:
        why.append("Your morning light was low, so the wake boost is the most important stage for you.")
    if res and "evening_light" in res["flags"]:
        why.append("You use bright light or screens late, so keep the warm dim and amber stages strict.")
    if not res:
        why.append("Standard plan. Take the Circadian calculator and it will adapt to your body clock.")
    dose = (f'<div class="card"><h2>Your light dose</h2><div class="sl-grid">' + stat("Bright light (250+ lux)", f"{bright_hours(blocks):.1f} h", "green")
            + stat("Dim and warm before bed", f"{sum(b['dur'] for b in blocks if b['name'] in ('Warm dim', 'Amber wind-down')) / 60:.1f} h")
            + stat("Dark sleep window", dur_txt(plan["need"] * 60)) + "</div>"
            + "<ul class=\"clean\" style=\"margin-top:.7rem\">" + "".join(f"<li>{escape(w)}</li>" for w in why) + "</ul></div>")
    out = lamp + sched + dose + site_form(d, plan["env"]) + solar_card(plan, d, blocks)
    day = ac.ENVIRONMENTS[plan["env"]]["day"]
    if day != 24.0:
        m = (day - 24) * 60
        trs = "".join(f'<tr><td>{"Today" if i == 0 else f"+{i} day" + ("s" if i > 1 else "")}</td><td>{hm(plan["wake"] + m * i)}</td><td>{hm(plan["bed"] + m * i)}</td></tr>' for i in range(8))
        out += (f'<div class="card"><h2>Day-length drift</h2><p>Your day is {day:.2f} hours, so your clock moves about {m:.0f} minutes later against Earth every day. '
                f'To keep pace, shift the whole lighting schedule and wake time later by the same amount.</p><table><tr><th>Earth day</th><th>Wake</th><th>Lights out</th></tr>{trs}</table></div>')
    return page("Light & solar exposure", out + LAMP_JS.replace("__BLOCKS__", js_json(jb)), "light")

def js_json(x):
    return json.dumps(x).replace("</", "<\\/")

@sleepcare.route("/light.json")
def light_json():
    plan = current_plan()
    blocks = light_blocks(plan)
    data = dict(generated=datetime.now().isoformat(timespec="minutes"), wake=hm(plan["wake"]), lights_out=hm(plan["bed"]), environment=plan["env"],
                schedule=[dict(stage=b["name"], start=hm(b["start"]), end=hm(b["end"]), kelvin=b["cct"], lux_min=b["lo"], lux_max=b["hi"]) for b in blocks])
    resp = jsonify(data)
    resp.headers["Content-Disposition"] = "attachment; filename=zenaut-light-schedule.json"
    return resp

# ------------------------------------------------------------------ Wind-down and relaxation
SLEEP_ROUTINES = {
    "Sleep: progressive muscle relaxation (~6 min)": [
        ("Settle", 30, "Get comfortable and secure. Dim the lights. Breathe in for 4, out for 6."),
        ("Feet and calves", 40, "Tense firmly for 5 seconds, then release for 30 seconds. Notice the difference."),
        ("Thighs and hips", 40, "Squeeze for 5 seconds, then let go completely."),
        ("Stomach and chest", 40, "Tighten for 5 seconds, release, and let your breath fall low and slow."),
        ("Hands and arms", 40, "Make fists and tense your arms for 5 seconds, then release and let them float."),
        ("Shoulders and neck", 40, "Lift your shoulders to your ears for 5 seconds, then drop them."),
        ("Face and jaw", 40, "Scrunch the whole face for 5 seconds, then soften. Let your jaw hang loose."),
        ("Whole body", 60, "Scan from head to toe. Let anything that is still tight let go with each out-breath."),
    ],
    "Sleep: worry dump and plan for tomorrow (~4 min)": [
        ("Write it out", 90, "On paper or in your Journal, list everything on your mind. Do not solve anything yet."),
        ("Pick the top three", 30, "Circle the three that matter most for tomorrow."),
        ("One next step each", 60, "Write one small action beside each. Now your brain can let go: it is captured."),
        ("Close the notebook", 20, "Say: \"That is for tomorrow. Tonight is for rest.\""),
        ("Slow breathing", 40, "Breathe in for 4, out for 6 until your thoughts slow down."),
    ],
    "Sleep: body rotation, yoga nidra style (~7 min)": [
        ("Settle and intend", 30, "Lie or float comfortably. Decide that you are only resting. Nothing to achieve."),
        ("Right hand and arm", 40, "Move your attention slowly: thumb, fingers, palm, wrist, forearm, elbow, shoulder."),
        ("Left hand and arm", 40, "Same path on the other side. Let each part feel heavy and warm."),
        ("Right leg", 40, "Toes, sole, ankle, calf, knee, thigh, hip. Soften as you go."),
        ("Left leg", 40, "Same path on the other side."),
        ("Back, belly, chest", 50, "Feel your back, then your belly rising, then your chest. Let go."),
        ("Neck, face, head", 50, "Jaw, tongue, cheeks, eyes, forehead, scalp. Feel the whole body at once."),
        ("Rest in the stillness", 60, "Let thoughts pass like clouds. Come back to the breath whenever you notice you wandered."),
    ],
    "Sleep: gentle stretch before bed (~5 min)": [
        ("Neck release", 40, "Slow half-circles, ear toward shoulder each side. Keep shoulders low."),
        ("Shoulder and chest opener", 40, "Clasp hands behind you, open the chest and breathe slowly."),
        ("Spine twist", 50, "Anchor your feet, turn your torso gently left, then right."),
        ("Hip and back release", 50, "Draw one knee toward your chest, then the other. Exhale as you stretch."),
        ("Hamstring and calf", 50, "Extend one leg and reach toward your toes. Switch halfway. No bouncing."),
        ("Slow finish", 40, "Breathe in for 4, out for 6 and let your body feel loose."),
    ],
    "Sleep: Earth from orbit imagery (~5 min)": [
        ("Breathe and soften", 40, "Slow breaths. Let your shoulders and jaw drop."),
        ("See the view", 60, "Picture Earth below you, blue and still. Notice the thin glow of the atmosphere."),
        ("Pick a place", 60, "Choose a place you love. Zoom slowly in until you see the details."),
        ("Add the senses", 60, "Add the sounds, the smell of the air, the warmth of the light on your skin."),
        ("Stay and sink", 80, "Stay in that place. With each out-breath, sink a little deeper into rest."),
    ],
}
BREATH_EXTRA = {"Slow exhale 4-6": [("Inhale", 4), ("Exhale", 6)], "Resonance 5.5-5.5": [("Inhale", 5.5), ("Exhale", 5.5)]}
BREATH_EXTRA_INFO = {"Slow exhale 4-6": "A longer out-breath calms the body. A good first step when you are wound up at night.",
                     "Resonance 5.5-5.5": "About 5.5 breaths a minute. Smooth and steady, nudges heart rate toward calm."}

STEP_HELP = {
    "dim": "Switch lights to warm and dim. Close shutters and finish bright tasks.",
    "blue": "Put screens on night mode, lowest brightness, or put them away. Amber or red lamps only.",
    "unwind": "Hygiene, tidy your area, stretch gently. Leave messages and work for tomorrow.",
    "relax": "Pick one routine below: breathing, muscle relaxation or body rotation.",
    "lights_out": "Lights off, mask on, white noise if you like. Stop trying to force sleep: rest is still useful.",
}

def relax_picks(res):
    picks = []
    if res and res["sq_worry"] >= 2:
        picks += [("Sleep: worry dump and plan for tomorrow (~4 min)", "you reported racing thoughts or worry at bedtime"),
                  ("Sleep: Earth from orbit imagery (~5 min)", "it gives a busy mind something calm to hold")]
    if res and res["sq_tense"] >= 2:
        picks += [("Sleep: progressive muscle relaxation (~6 min)", "you reported body tension at bedtime"),
                  ("Sleep: gentle stretch before bed (~5 min)", "it loosens a tense body")]
    for name, why in [("Sleep: body rotation, yoga nidra style (~7 min)", "a gentle all-round option"),
                      ("Sleep: wind-down for when you cannot sleep (~5 min)", "a quick routine for any night")]:
        if len(picks) < 4 and name not in [p[0] for p in picks]:
            picks.append((name, why))
    return picks

@sleepcare.route("/wind-down")
def winddown():
    d, ad = load(), ac.load()
    plan = current_plan(d, ad)
    res = plan["res"]
    ev = {e["key"]: e for e in day_events(plan)}
    order = ["caffeine", "dim", "blue", "unwind", "relax", "lights_out"]
    items = ""
    for k in order:
        e = ev[k]
        help_ = STEP_HELP.get(k, e["msg"])
        items += f'<li><span class="t">{e["time"]}</span><span><b>{escape(e["title"])}</b><br><span class="small">{escape(help_ if k != "caffeine" else e["msg"])}</span></span></li>'
    tl = f'<div class="card"><h2>Tonight\'s wind-down</h2><ul class="tl">{items}</ul></div>'
    flags = res["flags"] if res else []
    do = ["Keep the same bedtime and wake time every day", "Warm, dim, amber light in the last 3 hours", "A cool, dark, quiet sleep area (mask, earplugs, white noise)",
          "A short relaxation routine every night, not only when you struggle", "Write tomorrow's to-do list before you start winding down"]
    avoid = [("Bright and blue light, screens", "evening_light"), ("Caffeine in the 8 hours before bed", "caffeine"), ("Long or late naps", "naps"),
             ("Hard exercise or heavy meals in the last 3 hours", "exercise"), ("Checking messages or doing work in bed", None), ("Clock-watching while you try to sleep", None)]
    dl = "<ul class=\"clean\">" + "".join(f"<li>{escape(x)}</li>" for x in do) + "</ul>"
    flag_pill = ' <span class="pill hl">your answers flag this</span>'
    al = '<ul class="clean">' + "".join("<li>" + escape(t) + (flag_pill if k in flags else "") + "</li>" for t, k in avoid) + "</ul>"
    cards = f'<div class="two"><div class="card"><h2>Do</h2>{dl}</div><div class="card"><h2>Avoid</h2>{al}</div></div>'
    picks = relax_picks(res)
    pk = "".join(f'<li><b>{escape(n)}</b><br><span class="small">Suggested because {escape(w)}.</span></li>' for n, w in picks)
    picks_card = f'<div class="card"><h2>Relaxation exercises picked for you</h2><ul class="clean">{pk}</ul><p class="small">Choose them in the routine player below.</p></div>'
    breath = {**ac.BREATH, **BREATH_EXTRA}
    info = {**ac.BREATH_INFO, **BREATH_EXTRA_INFO}
    routines = {k: v for k, v in ac.ROUTINES.items() if k.startswith(("Sleep", "Grounding"))}
    routines.update(SLEEP_ROUTINES)
    ordered = {n: routines[n] for n, _ in picks if n in routines}
    ordered.update({k: v for k, v in routines.items() if k not in ordered})
    js = ac.RELAX_JS % (js_json(breath), js_json(info)) + ac.ROUTINES_JS % js_json(ordered)
    cant = (f'<form method="post" action="{url_for("astro.cant_sleep")}" style="margin-top:1rem"><button class="btn primary sosbtn">I can\'t sleep</button></form>')
    music_card = ('<div class="card"><h2>Sleep music</h2><p>Play your calming songs with a sleep timer that fades the music out.</p>'
                  f'<p><a class="btn primary" href="{url_for("music.player", q="Mood: Sleep")}">Open Sleep music</a></p></div>')
    body = (tl + cards + picks_card + music_card + ac.breathing_card("Breathing for sleep") + ac.routine_card("Guided relaxation routines")
            + '<div class="card"><h2>Grounding and more</h2><p>For anxiety, grounding and exercise routines, open '
            f'<a href="{url_for("astro.relax")}">Grounding and exercises</a>.</p>{cant}</div>'
            '<p class="small" style="margin-top:1rem">General wellbeing guidance, not medical advice. If you feel very low, anxious or confused, tell a crewmate and your flight surgeon.</p>' + js)
    return page("Wind-down & relaxation", body, "winddown")

# ------------------------------------------------------------------ Reminders
REMIND_JS = """
<script>
(function(){
  function $(i){return document.getElementById(i)}
  function st(k){try{return localStorage.getItem(k)}catch(e){return null}}
  function put(k,v){try{localStorage.setItem(k,v)}catch(e){}}
  function paint(){
    var on=st("zenaut_sleep_notify")==="on",perm=("Notification" in window)?Notification.permission:"unsupported";
    $("rstate").textContent=on?"Reminders are ON for this device.":"Reminders are OFF for this device.";
    $("rperm").textContent=perm==="granted"?"System notifications allowed.":perm==="denied"?"System notifications are blocked in your browser settings. Reminders will show inside ZENAUT instead.":
      perm==="unsupported"?"This browser has no system notifications. Reminders will show inside ZENAUT.":"System notifications not allowed yet (you will be asked).";
    $("ron").textContent=on?"Turn reminders off":"Turn reminders on";
    $("tint").checked=st("zenaut_sleep_tint")==="on";$("snd").checked=st("zenaut_sleep_sound")==="on";
  }
  $("ron").onclick=function(){
    if(st("zenaut_sleep_notify")==="on"){put("zenaut_sleep_notify","off");paint();return}
    put("zenaut_sleep_notify","on");
    if(window.Notification&&Notification.permission==="default"){Notification.requestPermission().then(paint)}else paint();
    if(window.ZenautSleep)window.ZenautSleep.refresh();
  };
  $("rtest").onclick=function(){if(window.ZenautSleep)window.ZenautSleep.test()};
  $("tint").onchange=function(){put("zenaut_sleep_tint",this.checked?"on":"off");if(window.ZenautSleep)window.ZenautSleep.tint()};
  $("snd").onchange=function(){put("zenaut_sleep_sound",this.checked?"on":"off")};
  document.querySelectorAll("[data-key]").forEach(function(c){
    c.onchange=function(){
      var fd=new FormData();fd.append("key",c.dataset.key);fd.append("on",c.checked?"1":"0");
      fetch(c.dataset.url,{method:"POST",body:fd}).then(function(){if(window.ZenautSleep)window.ZenautSleep.refresh()});
    };
  });
  paint();
})();
</script>"""

@sleepcare.route("/reminders")
def reminders():
    d = load()
    plan = current_plan(d)
    events = sorted(day_events(plan), key=lambda e: (e["at"] - plan["wake"]) % 1440)
    rows = "".join(f'<tr><td><b>{e["time"]}</b></td><td><b>{escape(e["title"])}</b><br><span class="small">{escape(e["msg"])}</span></td>'
                   f'<td><label class="switch"><input type="checkbox" data-key="{e["key"]}" data-url="{url_for("sleepcare.api_notify")}"'
                   f'{" checked" if d["notify"].get(e["key"], True) else ""} aria-label="Remind me: {escape(e["title"])}"></label></td></tr>' for e in events)
    ctl = ('<div class="card"><h2>Notifications on this device</h2><p id="rstate"></p><p class="small" id="rperm"></p>'
           '<div class="row"><button class="btn primary" id="ron" type="button">Turn reminders on</button><button class="btn" id="rtest" type="button">Send a test reminder</button></div>'
           '<div style="margin-top:.9rem;display:grid;gap:.6rem"><label class="switch"><input type="checkbox" id="tint">Warm the screen automatically in the evening (and dim it during your sleep window)</label>'
           '<label class="switch"><input type="checkbox" id="snd">Play a soft chime with reminders</label></div>'
           '<p class="small" style="margin-top:.7rem">Reminders appear while ZENAUT is open in a browser tab or window on this device. '
           'They cannot fire if the page is closed. Settings are saved on this device.</p></div>')
    table = f'<div class="card"><h2>Your reminders today</h2><div style="overflow-x:auto"><table><tr><th>Time</th><th>Reminder</th><th>On</th></tr>{rows}</table></div>' \
            '<p class="small" style="margin-top:.5rem">Times follow your wake time and bedtime. Change them on the Overview tab.</p></div>'
    return page("Sleep reminders", ctl + table + REMIND_JS, "reminders")

@sleepcare.route("/api/schedule")
def api_schedule():
    d = load()
    plan = current_plan(d)
    ev = day_events(plan)
    on = d["notify"]
    return jsonify(wake=plan["wake"], bed=plan["bed"], dim=240 if (plan["res"] and plan["res"]["sq_total"] >= 9) else 180,
                   events=[dict(key=e["key"], at=e["at"], time=e["time"], title=e["title"], msg=e["msg"],
                                href=url_for("sleepcare.light" if e["kind"] == "light" else "sleepcare.winddown"),
                                enabled=bool(on.get(e["key"], True))) for e in ev])

@sleepcare.route("/api/notify", methods=["POST"])
def api_notify():
    d = load()
    key = request.form.get("key", "")
    if key in {"wake_light", "caffeine", "dim", "blue", "unwind", "relax", "lights_out"}:
        d["notify"][key] = request.form.get("on") == "1"
        save(d)
    return jsonify(ok=True)
