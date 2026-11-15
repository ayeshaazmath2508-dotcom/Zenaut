"""ZENAUT · AstroCare features, ported from the AstroCare desktop prototype (Flask blueprint).

Fills dashboard tiles (the Journal tile lives in zenaut_cosmic.py). Everything is saved on this device (works offline):
  /astro/sleep      Now redirects to the Sleep & circadian centre at /sleepcare (zenaut_sleep.py)
  /astro/cant-sleep Exercises to fall asleep: 4-7-8 breathing, timed wind-down, body scan
  /astro/relax      Breathing patterns, 5-4-3-2-1 grounding, muscle relaxation                (Grounding tile)
  /astro/routines   Timed guided routines: body scan, focus reset, mobility, bone/muscle loading (Grounding tile)
  /astro/earth      Mission environment tips, messages home, "I feel seriously ill" button   (Connection with Earth > Mission & emergency tab)
Data goes to astrocare_data.json; messages and emergency alerts are queued as files in outbox/.
Not a medical device. Thresholds and advice are illustrative; have flight surgeons set real ones.
"""
import json
import random
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS, OUTBOX

astro = Blueprint("astro", __name__, url_prefix="/astro")
DATA = Path(__file__).with_name("astrocare_data.json")

ENVIRONMENTS = {
    "Spacecraft (transit)": dict(day=24.0,
        tips="Confinement + noise. Keep strict routines, use earplugs/white noise, schedule social time, "
             "view Earth/Moon images, keep personal space tidy.",
        sun="Light depends on trajectory; mostly artificial lighting, so rely on scheduled lighting."),
    "Space station (LEO)": dict(day=24.0,
        tips="Isolation from Earth but frequent calls. 16 sunrises/day disrupt sleep: use blinds + sleep masks. "
             "Take time at the window; the 'overview effect' helps mood.",
        sun="~16 sunrise/sunset cycles per day (~45 min light / ~45 min dark each)."),
    "Rover (surface)": dict(day=24.0,
        tips="Very cramped, long hours, dust. Alternate driving/rest, keep music and a crewmate check-in ritual, "
             "schedule hygiene and stretch breaks.",
        sun="Daylight follows the local planet/moon; plan sorties in daylight, rest in shelter."),
    "Moon surface": dict(day=24.0,
        tips="Harsh, gray, silent landscape. ~1.3 s comms delay is workable: live calls still possible. "
             "Use colorful/biophilic visuals and keep a 24h Earth clock.",
        sun="~14.75 Earth days of sunlight then ~14.75 days of night (polar sites differ). Use an artificial 24h light cycle."),
    "Mars surface": dict(day=24.66,
        tips="4-24 min comms delay: live calls impossible, so use recorded messages. Earth is just a dot "
             "(Earth-out-of-view effect): keep Earth photos, videos, journaling. Expect autonomy stress.",
        sun="Sol = 24h 39m 35s, ~12h daylight avg; dust storms dim light. Clocks drift ~40 min/day vs Earth: use a Mars-sol schedule."),
}
DEFAULT_ENV = "Space station (LEO)"

# ------------------------------------------------------------------ storage
def load():
    try:
        d = json.loads(DATA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        d = {}
    d.setdefault("journal", [])
    d.pop("chat", None)  # the chatbot was removed; drop any old chat history
    d.setdefault("cant_sleep", [])
    d.setdefault("wake", "07:00")
    d.setdefault("env", DEFAULT_ENV)
    if d["env"] not in ENVIRONMENTS:
        d["env"] = DEFAULT_ENV
    return d

def save(d):
    DATA.write_text(json.dumps(d, indent=2), encoding="utf-8")

def now_s():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

def outbox_add(kind, payload):
    """Queue an item as a JSON file in outbox/ (sent on the next sync or manual upload)."""
    OUTBOX.mkdir(exist_ok=True)
    ts = now_s()
    fn = OUTBOX / f"{ts.replace(':', '').replace(' ', '_')}_{kind}_{random.randint(100, 999)}.json"
    fn.write_text(json.dumps(dict(time=ts, type=kind, payload=payload), indent=2, default=str), encoding="utf-8")
    return fn.name

# ------------------------------------------------------------------ logic
def lighting_advice(minutes_since_wake, minutes_to_bed):
    h = minutes_since_wake / 60.0
    to_bed = minutes_to_bed / 60.0
    if h < 1:
        return "Bright, cool-white light (~6500K, 500+ lux) to wake the body clock."
    if h < 6:
        return "Bright neutral-cool light (~5000K, 300-500 lux). Good for focus."
    if to_bed > 3:
        return "Neutral light (~4000K, 200-300 lux)."
    if to_bed > 1:
        return "Warm dim light (~2700K, <100 lux). Reduce blue light."
    if to_bed > 0:
        return "WIND DOWN: very dim amber (~2200K, <30 lux). No bright screens."
    return "Sleep: lights off. Use red/amber night light only if needed."

def sleep_plan(wake_str, env):
    try:
        wake = datetime.strptime(wake_str, "%H:%M")
    except ValueError:
        return None
    bed = wake + timedelta(hours=16)
    info = ENVIRONMENTS[env]
    fm = lambda d: d.strftime("%H:%M")
    n = datetime.now().replace(year=1900, month=1, day=1)
    since = (n - wake).total_seconds() / 60 % (24 * 60)
    to_bed = (bed - n).total_seconds() / 60
    if to_bed < -480:
        to_bed += 24 * 60
    lines = [
        ("Wake", f"{fm(wake)} · bright cool light until {fm(wake + timedelta(hours=6))}"),
        ("Neutral light", f"until {fm(bed - timedelta(hours=3))}"),
        ("Warm dim light", f"{fm(bed - timedelta(hours=3))} to {fm(bed - timedelta(hours=1))}"),
        ("Wind-down", f"at {fm(bed - timedelta(hours=1))}: no bright/blue light, relax, white noise"),
        ("Target bedtime", f"{fm(bed)} · 8 h sleep"),
        ("Circadian low", f"core body temperature minimum around {fm(wake - timedelta(hours=2, minutes=30))}"),
    ]
    drift = None
    if info["day"] != 24.0:
        m = (info["day"] - 24) * 60
        drift = (f"Day length {info['day']:.2f} h: shift wake/bed time later by ~{m:.0f} min each Earth day "
                 f"(bed {fm(bed)}, +1 day {fm(bed + timedelta(minutes=m))}, +2 days {fm(bed + timedelta(minutes=2 * m))}).")
    return lines, lighting_advice(since, to_bed), drift

# ------------------------------------------------------------------ shell
CSS = """
.wb textarea{width:100%;min-height:110px;margin-top:.2rem;padding:.6rem;border-radius:10px;background:var(--space);
 color:var(--text);border:1px solid var(--grey);font:inherit;resize:vertical}
.wb .btn.primary{border:0}
.wb .entry{border-bottom:1px solid rgba(174,178,177,.2);padding:.6rem 0}.wb .entry:last-child{border:0}
.wb .entry .small{margin-bottom:.15rem}
.wb .step{font-size:1.15rem;font-weight:700;margin:.4rem 0 .4rem;min-height:5.5em}
.wb .sos{background:var(--bad);color:#fff;border:0;font-weight:700;padding:.7rem 1.4rem;border-radius:999px;font:inherit;font-weight:700;cursor:pointer}
.wb dl{margin:0}.wb dt{font-weight:700;margin-top:.5rem}.wb dd{margin:0;color:var(--grey)}
#breath{display:block;margin:0 auto}
.wb .apodcard{background:linear-gradient(160deg,#0a0d24,#17193a);color:#EAF0FF;overflow:hidden}
.wb .apodcard .small{color:#aab0d8}
.wb img.apod{width:100%;max-height:360px;object-fit:cover;border-radius:12px;margin-bottom:.7rem;display:block}
@media (prefers-reduced-motion:reduce){#breath{display:none}}
"""

def nav(active):
    """Tab links. Grounding and Guided routines link to each other; the Mission & emergency page sits beside Messages from home."""
    if active == "earth":
        items = [("earthlink.home", "Messages from home", False), ("astro.earth", "Mission & emergency", True)]
        return "".join(f'<a class="{"on" if on else ""}" href="{url_for(ep)}">{t}</a>' for ep, t, on in items)
    if active not in ("relax", "routines"):
        return ""
    items = [("relax", "Breathing & grounding"), ("routines", "Guided routines")]
    return "".join(f'<a class="{"on" if k == active else ""}" href="{url_for("astro." + k)}">{t}</a>' for k, t in items)

TAGS = {"sleep": "Circadian light plan", "cantsleep": "Exercises to help you fall asleep", "relax": "Breathing, grounding and relaxation",
        "routines": "Timed guided exercises", "earth": "Mission environment, messages to home and emergency"}

def page(heading, body, active, sidebar):
    return render_template("wellbeing.html", title=TAGS.get(active, "AstroCare"), heading=heading,
                           links=Markup(nav(active)), body=Markup(f"<style>{CSS}</style>{body}"),
                           css=Markup(WB_CSS), active=sidebar)

# ------------------------------------------------------------------ Sleep & light
@astro.route("/sleep", methods=["GET", "POST"])
def sleep():
    """The Sleep page moved to the Sleep & circadian centre (zenaut_sleep.py)."""
    return redirect(url_for("sleepcare.overview"))

# ------------------------------------------------------------------ Grounding / breathing / guided routines
BREATH = {
    "Box breathing (4-4-4-4)": [("Inhale", 4), ("Hold", 4), ("Exhale", 4), ("Hold", 4)],
    "4-7-8 wind-down": [("Inhale", 4), ("Hold", 7), ("Exhale", 8)],
    "Coherent 5-5": [("Inhale", 5), ("Exhale", 5)],
}
BREATH_INFO = {
    "Box breathing (4-4-4-4)": "Steadies heart rate and attention. Use before EVA, docking or tense tasks.",
    "4-7-8 wind-down": "Long exhale to switch toward sleep. Pair with dim, warm light.",
    "Coherent 5-5": "About 6 breaths a minute. A gentle daily reset.",
}
# (step, seconds, cue)
ROUTINES = {
    "Grounding: body scan + muscle release (~2 min)": [
        ("Settle in", 20, "Anchor your feet or strap in. Close your eyes. Notice where your body wants to float."),
        ("Feet and calves", 15, "Tense for 5 s, then release and let them drift."),
        ("Thighs and hips", 15, "Squeeze for 5 s, release slowly."),
        ("Core", 15, "Brace your stomach for 5 s, then let go. Breathe low and slow."),
        ("Hands and forearms", 15, "Make fists for 5 s, open and let the fingers float."),
        ("Shoulders and neck", 15, "Shrug up for 5 s, release. Roll your head gently."),
        ("Face and jaw", 15, "Scrunch for 5 s, release. Unclench your teeth."),
        ("Whole-body scan", 25, "Scan head to toe. Name one thing that feels okay right now."),
    ],
    "Mental: focus reset (~4 min)": [
        ("Count back by 7", 45, "From 100, aloud or silently. If you slip, restart without judgement."),
        ("Mental map of Earth", 45, "Picture your hometown from orbit. Trace your route home."),
        ("Gratitude trio", 45, "Name three good things from today, however small."),
        ("Safe place", 60, "Picture a calm place on Earth. Add sounds, smells and light."),
        ("Name the feeling", 30, "Say what you feel in one word. Naming it reduces its grip."),
        ("One next step", 20, "Choose one small, doable task for the next hour."),
    ],
    "Physical: mobility and posture circuit (~5 min)": [
        ("Neck rolls", 30, "Slow half-circles each way. Keep shoulders away from ears."),
        ("Shoulder circles", 30, "Big slow circles forward, then backward."),
        ("Spine twist", 40, "Anchor your feet, rotate your torso gently left and right."),
        ("Hip openers", 40, "Draw one knee to your chest, then the other. Breathe out as you stretch."),
        ("Hamstring and calf stretch", 50, "Extend one leg, reach toward your toes. Switch halfway."),
        ("Chest opener", 30, "Clasp hands behind you and open the chest to counter hunching."),
        ("Balance practice", 50, "Stay still eyes closed for 20 s, then eyes open. Trains the vestibular system."),
        ("Cool-down breathing", 40, "Slow coherent breaths. Notice your heart rate settling."),
    ],
    "Physical: bone and muscle loading (~7 min)": [
        ("Warm-up", 45, "Arm swings and leg swings, easy pace."),
        ("Squat pattern", 75, "Use your resistive device or bungees. Controlled, full range, steady breath."),
        ("Deadlift pattern", 75, "Hip hinge against resistance. Keep your spine neutral."),
        ("Press and row", 75, "Alternate pushing and pulling with bands or the resistive device."),
        ("Calf raises", 45, "Load heels and toes through the harness or footplate."),
        ("Core brace", 45, "Hold a hollow-body brace. Keep breathing; do not hold your breath."),
        ("Cool-down stretch", 75, "Stretch legs, back and shoulders. Breathe out slowly."),
    ],
}
ROUTINES["Sleep: wind-down for when you cannot sleep (~5 min)"] = [
    ("Dim and settle", 30, "Dim the lights to warm amber and put screens away. Get comfortable and secure."),
    ("Slow breathing", 60, "Breathe in for 4, out for 6. Let the out-breath be longer than the in-breath."),
    ("Release the body", 60, "From your feet up to your face: tense for 3 seconds, then let go completely."),
    ("Heavy and warm", 45, "Imagine your arms and legs growing heavy and warm. Let your bed or straps hold you."),
    ("Safe place", 60, "Picture a quiet place on Earth. Add the sounds, smells and light. Stay there."),
    ("Let thoughts drift", 60, "When a thought comes, label it 'thinking' and return to your breath. Do not try to force sleep."),
]
SENSES = [
    (5, "SEE", "a window view, a label, a Velcro strip, a floating droplet, your hands"),
    (4, "FEEL", "foot restraints, airflow on your face, fabric on skin, a handrail"),
    (3, "HEAR", "fans, pumps, your own breathing, a crewmate's voice"),
    (2, "SMELL", "clean cabin air, food, a wipe, fabric"),
    (1, "TASTE", "a sip of water, or the taste in your mouth"),
]

def js_json(x):
    """JSON that is safe inside a <script> tag."""
    return json.dumps(x).replace("</", "<\\/")

RELAX_JS = """
<script>
(function(){
  var PATTERNS=%s, INFO=%s;
  var sel=document.getElementById("bpat"),info=document.getElementById("binfo"),cv=document.getElementById("breath"),
      ctx=cv.getContext("2d"),phase=document.getElementById("bphase"),btn=document.getElementById("bstart"),
      run=false,t0=0,raf;
  var accent=getComputedStyle(document.querySelector(".wb")).getPropertyValue("--c0")||"#5FA8FF";
  Object.keys(PATTERNS).forEach(function(k){var o=document.createElement("option");o.textContent=k;sel.appendChild(o);});
  function showInfo(){info.textContent=INFO[sel.value];}
  function frame(){
    if(!run)return;
    var ph=PATTERNS[sel.value],total=ph.reduce(function(a,p){return a+p[1];},0),
        t=((performance.now()-t0)/1000)%%total,r=40;
    for(var i=0;i<ph.length;i++){
      var d=ph[i][1],label=ph[i][0];
      if(t<d){
        var f=t/d;
        r=label==="Inhale"?40+80*f:label==="Exhale"?120-80*f:(i>0&&ph[i-1][0]==="Inhale"?120:40);
        phase.textContent=label+" "+Math.ceil(d-t);
        break;
      }
      t-=d;
    }
    ctx.clearRect(0,0,300,260);ctx.beginPath();ctx.arc(150,130,r,0,6.283);ctx.fillStyle=accent;ctx.fill();
    raf=requestAnimationFrame(frame);
  }
  function stop(){run=false;cancelAnimationFrame(raf);ctx.clearRect(0,0,300,260);phase.textContent="Ready";btn.textContent="Start";}
  btn.onclick=function(){
    if(run){stop();}else{run=true;t0=performance.now();btn.textContent="Stop";frame();}
  };
  sel.onchange=function(){showInfo();if(run){t0=performance.now();}};
  showInfo();
  document.querySelectorAll("[data-steps]").forEach(function(box){
    var steps=JSON.parse(box.dataset.steps),i=0,out=box.querySelector(".step"),cnt=box.querySelector(".cnt"),
        back=box.querySelector(".back"),next=box.querySelector(".next");
    function show(){out.textContent=steps[i];cnt.textContent=(i+1)+" / "+steps.length;
      back.disabled=i===0;next.textContent=i===steps.length-1?"Start again":"Next";}
    back.onclick=function(){if(i>0){i--;show();}};
    next.onclick=function(){i=(i+1)%%steps.length;show();};
    show();
  });
})();
</script>"""  # %s placeholders and %% are filled in relax()

def stepper(title, steps, intro=""):
    return (f'<div class="card" data-steps="{escape(json.dumps(steps))}">'
            f'<h2>{escape(title)}</h2>' + (f'<p class="small">{escape(intro)}</p>' if intro else "") +
            '<div class="step"></div><p class="small cnt"></p>'
            '<button class="btn back" type="button">Back</button> <button class="btn primary next" type="button">Next</button></div>')

TECHNIQUES = [
    ("5-4-3-2-1 senses", "Pulls your attention out of racing thoughts and into the room around you.",
     [f"{n}: name {n} thing{'s' if n > 1 else ''} you can {what}.  e.g. {ex}" for n, what, ex in SENSES]
     + ["Done. Take one slow breath in, and a longer one out."]),
    ("Feet on the ground", "Use your body's contact with the floor to feel steady. In microgravity, use your foot restraints.",
     ["Plant your feet. On a surface: feel the floor under both feet. In microgravity: slide them into the foot restraints or press them against a handrail.",
      "Press down gently for 5 seconds, then release. Notice the pressure and where your feet make contact.",
      "Wiggle your toes. Feel each one. Notice the temperature and texture under and around your feet.",
      "Feel your weight (or the pull of the restraints) through your heels, the balls of your feet and your toes.",
      "Silently say: \"My feet are here. I am here. I can handle this moment.\"",
      "Take three slow breaths. With each exhale, let the tension drain down through your feet."]),
    ("Hold an anchor object", "A familiar object gives your senses something solid to focus on.",
     ["Pick up something small and familiar: a mission patch, a photo, a pen, a smooth object from your kit.",
      "Look at it closely. Name its colors, shape and any marks on it.",
      "Feel it. Is it smooth, rough, warm, cool, light or heavy? Run your thumb over its surface.",
      "Describe it to yourself in three full sentences, as if to a crewmate who has never seen it.",
      "Hold it while you take three slow breaths."]),
    ("Orient yourself", "Reminds your mind where and when you are.",
     ["Say your name and your role on this mission.",
      "Say where you are right now: the module, the vehicle or the habitat.",
      "Say today's date and your mission day.",
      "Name the crewmates nearby, or the ones you will speak to next.",
      "Name one thing you are about to do next, and the first small step."]),
    ("Category game", "Occupies a racing mind with something easy and neutral.",
     ["Choose a category: planets, countries, foods or songs.",
      "Name 5 items in it, slowly.",
      "Now choose a second category and name 5 more.",
      "Count back from 50 in steps of 3.",
      "Take one slow breath and notice if your mind feels quieter."]),
    ("Progressive muscle relaxation", "Tense, then release, one body area at a time.",
     ["Tense feet and calves for 5 s... release", "Tense thighs and glutes for 5 s... release",
      "Tense stomach and chest for 5 s... release", "Tense hands and arms for 5 s... release",
      "Scrunch shoulders and face for 5 s... release", "Breathe slowly: let your whole body go soft"]),
]

@astro.route("/relax")
def relax():
    cards = "".join(stepper(t, steps, intro) for t, intro, steps in TECHNIQUES)
    js = RELAX_JS % (js_json(BREATH), js_json(BREATH_INFO))
    body = (breathing_card() +
            '<h2 style="margin-top:1.4rem">Grounding techniques</h2>'
            '<p class="small">Work through one card at a time. Use Next when you are ready for the following step.</p>'
            f'<div class="grid">{cards}</div>'
            '<p class="small" style="margin-top:1rem">If these feel overwhelming or you feel seriously unwell, tell a crewmate and your flight surgeon.</p>' + js)
    return page("Grounding and exercises", body, "relax", "grounding")

def breathing_card(title="Breathing"):
    return (f'<div class="card"><h2>{escape(title)}</h2>'
            '<label>Pattern<select id="bpat"></select></label><p class="small" id="binfo"></p>'
            '<canvas id="breath" width="300" height="260" aria-hidden="true"></canvas>'
            '<p class="step" id="bphase" style="text-align:center;min-height:0" aria-live="polite">Ready</p>'
            '<p style="text-align:center"><button class="btn primary" id="bstart" type="button">Start</button></p></div>')

def routine_card(title="Guided routines"):
    return (f'<div class="card"><h2>{escape(title)}</h2>'
            '<label>Routine<select id="rsel"></select></label><p class="small" id="rsum"></p>'
            '<div class="meter"><i id="rbar" class="green" style="width:0"></i></div>'
            '<h3 id="rtitle" style="margin:1rem 0 0;text-align:center"></h3>'
            '<div class="stat" id="rcount" style="text-align:center;font-size:3.5rem" aria-live="off"></div>'
            '<p id="rcue" style="text-align:center;min-height:3em"></p>'
            '<p style="text-align:center"><button class="btn primary" id="rgo" type="button">Start</button> '
            '<button class="btn" id="rreset" type="button">Reset</button></p></div>')

ROUTINES_JS = """
<script>
(function(){
  var R=%s,sel=document.getElementById("rsel"),go=document.getElementById("rgo"),reset=document.getElementById("rreset"),
      title=document.getElementById("rtitle"),count=document.getElementById("rcount"),cue=document.getElementById("rcue"),
      bar=document.getElementById("rbar"),sum=document.getElementById("rsum"),timer=null,i=0,left=0,done=0,total=0,paused=true;
  Object.keys(R).forEach(function(k){var o=document.createElement("option");o.textContent=k;sel.appendChild(o);});
  function steps(){return R[sel.value];}
  function secs(s){var m=Math.floor(s/60),r=s%%60;return m+":"+(r<10?"0":"")+r;}
  function draw(){
    var s=steps()[i];
    title.textContent=s[0]+"  (step "+(i+1)+" of "+steps().length+")";
    count.textContent=secs(left);cue.textContent=s[2];
    bar.style.width=(total?100*done/total:0)+"%%";
  }
  function setup(){
    clearInterval(timer);timer=null;paused=true;i=0;done=0;
    total=steps().reduce(function(a,s){return a+s[1];},0);left=steps()[0][1];
    sum.textContent="Total about "+secs(total)+".";go.textContent="Start";draw();
  }
  function tick(){
    left--;done++;
    if(left<=0){
      i++;
      if(i>=steps().length){
        clearInterval(timer);timer=null;i=steps().length-1;left=0;done=total;draw();
        title.textContent="Routine complete";count.textContent="Well done";cue.textContent="Notice how you feel now.";
        go.textContent="Start again";paused=true;i=0;left=steps()[0][1];done=0;
        return;
      }
      left=steps()[i][1];
    }
    draw();
  }
  go.onclick=function(){
    if(paused){
      if(done===0&&i===0&&!timer)draw();
      paused=false;go.textContent="Pause";timer=setInterval(tick,1000);
    }else{paused=true;clearInterval(timer);timer=null;go.textContent="Resume";}
  };
  reset.onclick=setup;sel.onchange=setup;setup();
})();
</script>"""

@astro.route("/routines")
def routines():
    body = (routine_card() +
            '<p class="small" style="margin-top:1rem">Exercise routines are general guidance, not a prescription. '
            'Follow your mission exercise plan and stop if you feel unwell.</p>'
            + ROUTINES_JS % js_json(ROUTINES))
    return page("Guided routines", body, "routines", "grounding")

# ------------------------------------------------------------------ Can't sleep
SLEEP_ROUTINES = ["Sleep: wind-down for when you cannot sleep (~5 min)", "Grounding: body scan + muscle release (~2 min)"]
SLEEP_BREATH = ["4-7-8 wind-down", "Coherent 5-5"]

@astro.route("/cant-sleep", methods=["GET", "POST"])
def cant_sleep():
    if request.method == "POST":
        d = load()
        d["cant_sleep"] = (d["cant_sleep"] + [now_s()])[-60:]
        save(d)
        return redirect(url_for("astro.cant_sleep"))
    breath = {k: BREATH[k] for k in SLEEP_BREATH}
    routines_ = {k: ROUTINES[k] for k in SLEEP_ROUTINES}
    js = RELAX_JS % (js_json(breath), js_json({k: BREATH_INFO[k] for k in SLEEP_BREATH})) + ROUTINES_JS % js_json(routines_)
    body = ('<div class="card"><h2>Can\'t sleep? That is common in space.</h2>'
            '<p>Do these in order. Keep the lights dim and warm, and keep screens away. '
            'You do not have to fall asleep: resting quietly still helps your body recover.</p></div>'
            + breathing_card("1. Slow your breathing (4-7-8 is best)")
            + routine_card("2. Relax your body and mind")
            + '<div class="card"><h2>3. If you are still awake after about 20 minutes</h2><ul class="clean">'
              '<li>Do not watch the clock. Turn it away or cover it.</li>'
              '<li>Get up, keep the light very dim and amber, and do something quiet: stretch gently, or read something calm.</li>'
              '<li>Go back to your sleep area when you feel sleepy again.</li>'
              '<li>Play white noise or use earplugs and a sleep mask to cut cabin noise and light.</li>'
              '<li>Skip caffeine and heavy meals before sleep, and keep your wake-up time steady tomorrow.</li></ul></div>'
              '<div class="card"><h2>When to ask for help</h2>'
              '<p>If you struggle to sleep for several nights in a row, or you feel very low, anxious or confused, '
              'tell your flight surgeon or the behavioral health team.</p></div>'
              f'<p><a class="btn" href="{url_for("sleepcare.overview")}">Back to Sleep &amp; circadian</a></p>' + js)
    return page("Can't sleep", body, "cantsleep", "sleep")

# ------------------------------------------------------------------ Earth, environment, SOS
@astro.route("/earth", methods=["GET", "POST"])
def earth():
    d = load()
    note = ""
    if request.method == "POST":
        form = request.form.get("form")
        if form == "env" and request.form.get("env") in ENVIRONMENTS:
            d["env"] = request.form["env"]
            save(d)
        elif form == "message" and request.form.get("text", "").strip():
            outbox_add("MESSAGE_OUT", dict(text=request.form["text"].strip()[:2000]))
            note = '<div class="alert green">Message saved to the outbox. It will send when a link is available.</div>'
    env = ENVIRONMENTS[d["env"]]
    opts = "".join(f'<option{" selected" if e == d["env"] else ""}>{escape(e)}</option>' for e in ENVIRONMENTS)
    files = sorted(OUTBOX.glob("*.json"), reverse=True)[:15] if OUTBOX.exists() else []
    queue = "".join(f"<li>{escape(f.name)}</li>" for f in files) or '<li class="small">Nothing queued.</li>'
    body = (note +
            '<div class="card"><h2>Mission environment</h2><form method="post" class="log"><input type="hidden" name="form" value="env">'
            f'<label>Where are you?<select name="env">{opts}</select></label>'
            '<div style="align-self:end"><button class="btn">Update</button></div></form>'
            f'<dl><dt>Mental-health tips</dt><dd>{escape(env["tips"])}</dd><dt>Light and sun</dt><dd>{escape(env["sun"])}</dd>'
            f'<dt>Day length</dt><dd>{env["day"]} h</dd></dl></div>'
            '<div class="card"><h2>Message to loved ones</h2><form method="post"><input type="hidden" name="form" value="message">'
            '<textarea name="text" placeholder="Write a message to send home" required></textarea>'
            '<p><button class="btn primary">Queue message</button></p></form></div>'
            '<div class="card"><h2>If you feel seriously ill</h2><form method="post" action="' + url_for("astro.sos") + '">'
            '<label>Describe symptoms (e.g. chest pain, severe headache, fainting)<textarea name="symptoms" required></textarea></label>'
            '<p><button class="sos">I feel seriously ill</button></p></form></div>'
            f'<div class="card"><h2>Outbox</h2><ul class="clean">{queue}</ul>'
            '<p class="small">Queued items are JSON files in the <code>outbox</code> folder. Copy them over by USB or local link, '
            'or send them on the next sync. There is no live link to a medical team yet.</p></div>')
    return page("Connection with Earth", body, "earth", "earth")

def vitals_snapshot():
    """Best effort: latest readings from the Physical health page."""
    try:
        from zenaut_physical import READINGS, load as pload, series_of
        d = pload()
        return {slug: rows[-1] for slug in READINGS if (rows := series_of(d, slug))}
    except Exception:
        return {}

@astro.route("/earth/sos", methods=["POST"])
def sos():
    sym = request.form.get("symptoms", "").strip()[:2000]
    if not sym:
        return redirect(url_for("astro.earth"))
    outbox_add("EMERGENCY", dict(symptoms=sym, latest_vitals=vitals_snapshot()))
    body = ('<div class="alert red"><b>Emergency alert saved and queued for the flight surgeon and medical contacts.</b></div>'
            '<div class="card"><h2>Do now</h2><ol class="clean">'
            '<li>Stop activity, secure yourself, tell a crewmate.</li>'
            '<li>Open the onboard medical kit/checklist; get an AED if chest pain or collapse.</li>'
            '<li>Open the voice/data channel to the flight surgeon.</li>'
            '<li>Keep taking pulse + SpO2 and write the numbers down.</li>'
            '<li>Stay calm: slow breathing, someone stays with you.</li></ol></div>'
            f'<p><a class="btn" href="{url_for("astro.earth")}">Back</a></p>')
    return page("Emergency", body, "earth", "earth")
