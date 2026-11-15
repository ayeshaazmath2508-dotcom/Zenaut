"""ZENAUT · Cosmic Journal: a separate full-screen "astronaut world" with three sections (Flask blueprint).

  Sky & jokes   NASA picture of the day (fetched when online, then saved on this device) + space jokes
  Journal       Mood, energy, tags, gratitude, prompts, search and filter, favourites, streak and mood trend
  Loved ones    People you write to, messages saved on this device and queued in outbox/ for the next sync

Works offline. Only the NASA picture needs internet, and only once a day; the last picture is kept.
Get your own free key at https://api.nasa.gov and set it as the NASA_API_KEY environment variable
(DEMO_KEY works but is rate limited).
Data is stored in astrocare_data.json (same file as the other AstroCare pages).
"""
import json
import os
import urllib.request
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from zenaut_astrocare import load, now_s, outbox_add, save

cosmic = Blueprint("cosmic", __name__, url_prefix="/cosmic")
APOD_CACHE = Path(__file__).with_name("apod_cache.json")
APOD_IMG = Path(__file__).with_name("apod_image.bin")
IMG_TYPES = ("image/jpeg", "image/png", "image/gif", "image/webp")
IMG_MAX = 12 * 1024 * 1024
OFFLINE_NOTE = "Offline - the stars are still there ✨"

TABS = [("sky", "✦ Sky & jokes"), ("journal", "✎ Journal"), ("loved", "♥ Loved ones")]
MOODS = {1: "😞", 2: "😕", 3: "😐", 4: "🙂", 5: "😄"}
MOOD_WORDS = {1: "Rough", 2: "Low", 3: "Okay", 4: "Good", 5: "Great"}
TAGS = ["Work", "EVA", "Exercise", "Crew", "Family", "Science", "Food", "Sleep", "Earth", "Health"]

JOKES = [
    "Why did the sun go to school? To get a little brighter.",
    "How does the solar system hold up its pants? With an asteroid belt.",
    "What did the astronaut say when he walked into the kitchen? \"Houston, we have a snack problem.\"",
    "Why didn't the Moon finish its dinner? It was already full.",
    "How do you organise a space party? You planet.",
    "What is an astronaut's favourite part of a computer? The space bar.",
    "Why is Saturn the best dancer? It really knows how to ring in the fun.",
    "Why did the star get sent to its room? It was acting like a supernova.",
    "What do you call a tick on the Moon? A luna-tick.",
    "Why was the rocket so confident? It had a great launch pad.",
    "What kind of music do planets like? Neptunes.",
    "Why did the Mars rover feel lonely? It had too much space.",
    "How does an astronaut say goodbye? \"See you later, orbiter!\"",
    "What did Mars say to Saturn? \"Give me a ring sometime.\"",
    "What do you call an alien with three eyes? An aliiien.",
    "Why do astronauts never get hungry on the Moon? Because they bring a lunch module.",
    "Why did the cow go into orbit? To see the Milky Way.",
    "What did the Earth say to the Sun? \"Stop being so hot, I need my space.\"",
    "How do you know the Moon is going broke? It's down to its last quarter.",
    "Why did the astronaut break up with the Sun? Too many flares.",
    "What is a spaceship's favourite snack? Rocket fuel chips.",
    "Why was the telescope such a good friend? It always helped you see the bigger picture.",
    "What's the best way to see a cosmic joke? Zoom out.",
    "Why didn't the Sun go to university? It already had millions of degrees.",
    "What did the alien say to the garden? \"Take me to your weeder.\"",
    "Why do astronauts never feel crowded? They have plenty of space.",
    "What did the astronaut say when the rocket was late? \"I'm feeling a little launch-less.\"",
]

PROMPTS = [
    "What was the best moment of today, however small?",
    "Describe the view or the light around you right now.",
    "What is one thing you learned today?",
    "Who are you thinking about, and what would you tell them?",
    "What is weighing on you? Write it down and let it go.",
    "What did your body need today, and did it get it?",
    "Write about a smell, taste or sound from Earth that you miss.",
    "What are you proud of this week?",
    "What is one thing you are looking forward to?",
    "Describe a crewmate's small kindness.",
    "If you could send one photo home today, what would it show?",
    "What would you tell yourself on launch day?",
]

LETTER_STARTERS = {
    "Miss you": "Hi, I'm thinking about you. I miss you a lot, and I wanted you to know that.",
    "I'm okay": "Just a quick note to say I'm okay. The work is going well and I'm taking care of myself.",
    "Good news": "I have some good news to share with you!",
    "Tough day": "Today was a hard one, and I wanted to share it with you. I'm okay, I just needed to say it out loud.",
    "Thank you": "Thank you for being there for me. It means more than I can say from up here.",
}


# ------------------------------------------------------------------ NASA picture (cached on this device)
def read_apod_cache():
    try:
        return json.loads(APOD_CACHE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def fetch_image(url):
    """Download the picture so it still shows offline. Returns its content type or ''."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "ZENAUT"})
        with urllib.request.urlopen(req, timeout=10) as r:
            ctype = r.headers.get_content_type()
            if ctype not in IMG_TYPES:
                return ""
            data = r.read(IMG_MAX + 1)
        if len(data) > IMG_MAX:
            return ""
        APOD_IMG.write_bytes(data)
        return ctype
    except Exception:
        return ""


def get_apod():
    """Today's NASA picture, fetched at most once a day. Returns a dict, or None when offline with nothing cached."""
    c = read_apod_cache()
    today = date.today().isoformat()
    if c.get("fetched") == today and c.get("title"):
        return c
    if c.get("failed_at") == today and c.get("failed_hour") == datetime.now().hour:
        return c if c.get("title") else None            # do not hammer the network while offline
    try:
        key = os.environ.get("NASA_API_KEY", "DEMO_KEY")
        with urllib.request.urlopen(f"https://api.nasa.gov/planetary/apod?api_key={key}&thumbs=true", timeout=4) as r:
            res = json.loads(r.read().decode("utf-8"))
        img = res.get("url") if res.get("media_type") == "image" else res.get("thumbnail_url")
        img = img if isinstance(img, str) and img.startswith("https://") else ""
        c = dict(fetched=today, title=str(res.get("title", "Space")), image=img,
                 explanation=str(res.get("explanation", "")), credit=str(res.get("copyright", "NASA")).strip(),
                 apod_date=str(res.get("date", "")), link=str(res.get("url", "")),
                 img_type=fetch_image(img) if img else "")
    except Exception:
        c["failed_at"], c["failed_hour"] = today, datetime.now().hour
        APOD_CACHE.write_text(json.dumps(c), encoding="utf-8")
        return c if c.get("title") else None
    APOD_CACHE.write_text(json.dumps(c), encoding="utf-8")
    return c


def safe_url(u):
    return u if isinstance(u, str) and u.startswith("https://") else ""


@cosmic.route("/apod-image")
def apod_image():
    c = read_apod_cache()
    if c.get("img_type") not in IMG_TYPES or not APOD_IMG.exists():
        abort(404)
    return send_file(APOD_IMG, mimetype=c["img_type"], max_age=3600)


# ------------------------------------------------------------------ helpers
def go(tab, **kw):
    return redirect(url_for("cosmic.page", tab=tab, **kw))


def migrate(d):
    """Give older journal entries an id so they can be starred or deleted."""
    changed = False
    for e in d["journal"]:
        if "id" not in e:
            e["id"] = uuid.uuid4().hex[:10]
            changed = True
    for k in ("people", "letters"):
        if k not in d:
            d[k] = []
            changed = True
    if changed:
        save(d)


def pretty_ts(ts):
    try:
        return datetime.strptime(ts, "%Y-%m-%d %H:%M:%S").strftime("%d %b %Y, %H:%M")
    except ValueError:
        return ts


def streak(entries):
    days = {e["ts"][:10] for e in entries}
    day = date.today()
    if day.isoformat() not in days:
        day -= timedelta(days=1)
    n = 0
    while day.isoformat() in days:
        n += 1
        day -= timedelta(days=1)
    return n


def sparkline(values, w=300, h=60):
    """Tiny inline SVG mood trend (values 1-5, oldest first)."""
    if len(values) < 2:
        return ""
    pts = []
    for i, v in enumerate(values):
        x = 6 + i * (w - 12) / (len(values) - 1)
        y = h - 6 - (v - 1) / 4 * (h - 12)
        pts.append((x, y))
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in pts)
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="#CCC0FF"/>' for x, y in pts)
    return Markup(f'<svg viewBox="0 0 {w} {h}" width="100%" height="{h}" role="img" aria-label="Mood trend">'
                  f'<polyline points="{line}" fill="none" stroke="#734DE6" stroke-width="2.5" stroke-linejoin="round"/>{dots}</svg>')


def clean_int(v, lo, hi):
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if lo <= n <= hi else None


# ------------------------------------------------------------------ page
@cosmic.route("/")
def page():
    tab = request.args.get("tab", "sky")
    if tab not in dict(TABS):
        tab = "sky"
    d = load()
    migrate(d)
    ctx = dict(tab=tab, tabs=TABS, back=url_for("dashboard"), moods=MOODS, mood_words=MOOD_WORDS, tags=TAGS)

    if tab == "sky":
        ap = get_apod()
        local = bool(ap and ap.get("img_type") in IMG_TYPES and APOD_IMG.exists())
        if local:
            img = url_for("cosmic.apod_image", d=ap.get("fetched", ""))
        else:
            img = safe_url(ap.get("image")) if ap else ""
        ctx.update(ap=ap, img=img, link=safe_url(ap.get("link")) if ap else "", offline=OFFLINE_NOTE,
                   jokes=JOKES, joke=JOKES[date.today().toordinal() % len(JOKES)])

    elif tab == "journal":
        q = request.args.get("q", "").strip()[:80]
        tag = request.args.get("tag", "")
        allx = d["journal"]
        shown = []
        for e in reversed(allx):
            if tag and tag not in e.get("tags", []):
                continue
            if q:
                hay = " ".join([e.get("title", ""), e.get("text", ""), " ".join(e.get("gratitude", []))]).lower()
                if q.lower() not in hay:
                    continue
            shown.append(dict(e, ts_pretty=pretty_ts(e["ts"])))
        week = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        wk = [e["mood"] for e in allx if e.get("mood") and e["ts"] >= week]
        trend = [e["mood"] for e in allx if e.get("mood")][-14:]
        ctx.update(entries=shown[:60], total=len(allx), streak=streak(allx), q=q, tag=tag,
                   avg=(f"{sum(wk) / len(wk):.1f}" if wk else "-"), spark=sparkline(trend),
                   prompt=PROMPTS[date.today().toordinal() % len(PROMPTS)], prompts=PROMPTS,
                   ap_title=read_apod_cache().get("title", ""))

    else:
        ctx.update(people=d["people"], letters=[dict(l, ts_pretty=pretty_ts(l["ts"])) for l in reversed(d["letters"][-60:])],
                   starters=LETTER_STARTERS)
    return render_template("cosmic_journal.html", **ctx)


# ------------------------------------------------------------------ journal actions
@cosmic.route("/entry", methods=["POST"])
def add_entry():
    f = request.form
    text = f.get("text", "").strip()[:5000]
    grat = [g.strip()[:200] for g in f.getlist("gratitude") if g.strip()][:3]
    if not text and not grat:
        return go("journal")
    d = load()
    migrate(d)
    d["journal"].append(dict(
        id=uuid.uuid4().hex[:10], ts=now_s(), title=f.get("title", "").strip()[:80], text=text,
        mood=clean_int(f.get("mood"), 1, 5), energy=clean_int(f.get("energy"), 1, 5),
        tags=[t for t in f.getlist("tags") if t in TAGS], gratitude=grat, fav=False,
        apod=read_apod_cache().get("title", "")))
    save(d)
    return go("journal")


@cosmic.route("/entry/<eid>/fav", methods=["POST"])
def fav_entry(eid):
    d = load()
    for e in d["journal"]:
        if e.get("id") == eid:
            e["fav"] = not e.get("fav", False)
    save(d)
    return go("journal", q=request.form.get("q", ""), tag=request.form.get("tag", ""))


@cosmic.route("/entry/<eid>/delete", methods=["POST"])
def delete_entry(eid):
    d = load()
    d["journal"] = [e for e in d["journal"] if e.get("id") != eid]
    save(d)
    return go("journal", q=request.form.get("q", ""), tag=request.form.get("tag", ""))


# ------------------------------------------------------------------ loved ones actions
@cosmic.route("/people", methods=["POST"])
def add_person():
    name = request.form.get("name", "").strip()[:60]
    if name:
        d = load()
        migrate(d)
        d["people"].append(dict(id=uuid.uuid4().hex[:10], name=name, relation=request.form.get("relation", "").strip()[:40]))
        save(d)
    return go("loved")


@cosmic.route("/people/<pid>/delete", methods=["POST"])
def delete_person(pid):
    d = load()
    d["people"] = [p for p in d["people"] if p.get("id") != pid]
    save(d)
    return go("loved")


@cosmic.route("/letters", methods=["POST"])
def send_letter():
    text = request.form.get("text", "").strip()[:4000]
    if not text:
        return go("loved")
    d = load()
    migrate(d)
    pid = request.form.get("to", "")
    person = next((p for p in d["people"] if p["id"] == pid), None)
    to_name = person["name"] if person else "My loved ones"
    outbox_file = outbox_add("MESSAGE_OUT", dict(to=to_name, text=text))
    d["letters"].append(dict(id=uuid.uuid4().hex[:10], ts=now_s(), to_name=to_name, text=text, file=outbox_file))
    save(d)
    return go("loved")


@cosmic.route("/letters/<lid>/delete", methods=["POST"])
def delete_letter(lid):
    d = load()
    d["letters"] = [l for l in d["letters"] if l.get("id") != lid]
    save(d)
    return go("loved")
