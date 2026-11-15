"""ZENAUT · Music, an offline music player for the dashboard (Flask blueprint at /music).

Ported from your Streamlit version: same library, moods, playlists, favourites, shuffle, repeat and sleep timer.
Songs are copied into ./zenaut_music_data/ on this device and streamed from there, so no internet is needed.
Suggestions use your Mood Monitor check-ins, last night's sleep (Sleep monitor) and the time of day.
Not medical advice: music is a wellbeing aid, not a treatment.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

from flask import Blueprint, abort, jsonify, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

music = Blueprint("music", __name__, url_prefix="/music")

BASE = Path(__file__).with_name("zenaut_music_data")
FILES = BASE / "files"
DB_FILE = BASE / "library.json"

MOODS = ["Calm", "Focus", "Energize", "Sleep", "Memories from home"]
ALLOWED = ["mp3", "wav", "ogg", "m4a", "aac", "flac", "opus"]
MIME = {"mp3": "audio/mpeg", "wav": "audio/wav", "ogg": "audio/ogg", "m4a": "audio/mp4",
        "aac": "audio/aac", "flac": "audio/flac", "opus": "audio/ogg"}
TABS = [("player", "Now playing"), ("library", "Library"), ("playlists", "Playlists"), ("upload", "Upload")]

# ------------------------------------------------------------------ storage
def load_db():
    FILES.mkdir(parents=True, exist_ok=True)
    try:
        db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        db = {}
    db.setdefault("tracks", [])
    db.setdefault("playlists", {})
    return db

def save_db(db):
    FILES.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(db, indent=2), encoding="utf-8")

def clean_title(filename):
    stem = Path(filename).stem
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Untitled"

def add_track(db, filename, data, mood, artist):
    """Copy an uploaded song into the library. Returns the track, or None if duplicate or unsupported."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED or not data:
        return None
    digest = hashlib.sha1(data).hexdigest()
    if any(t["hash"] == digest for t in db["tracks"]):
        return None
    tid = uuid.uuid4().hex[:10]
    (FILES / f"{tid}.{ext}").write_bytes(data)
    track = {"id": tid, "title": clean_title(filename)[:120], "artist": artist.strip()[:80] or "Unknown artist",
             "mood": mood, "ext": ext, "hash": digest, "favorite": False, "plays": 0,
             "size": len(data), "added": datetime.now().strftime("%Y-%m-%d %H:%M")}
    db["tracks"].append(track)
    return track

def delete_track(db, tid):
    for t in db["tracks"]:
        if t["id"] == tid:
            (FILES / f"{tid}.{t['ext']}").unlink(missing_ok=True)
    db["tracks"] = [t for t in db["tracks"] if t["id"] != tid]
    for name in db["playlists"]:
        db["playlists"][name] = [i for i in db["playlists"][name] if i != tid]

def find(db, tid):
    return next((t for t in db["tracks"] if t["id"] == tid), None)

def queue_options(db):
    return ["All songs", "Favorites", "Recently added"] + [f"Mood: {m}" for m in MOODS] + [f"Playlist: {n}" for n in db["playlists"]]

def queue_for(db, key):
    tracks = db["tracks"]
    if key == "All songs":
        return list(tracks)
    if key == "Favorites":
        return [t for t in tracks if t["favorite"]]
    if key == "Recently added":
        return sorted(tracks, key=lambda t: t["added"], reverse=True)[:15]
    if key.startswith("Mood: "):
        return [t for t in tracks if t["mood"] == key[6:]]
    if key.startswith("Playlist: "):
        lookup = {t["id"]: t for t in tracks}
        return [lookup[i] for i in db["playlists"].get(key[10:], []) if i in lookup]
    return []

def fmt_size(n):
    return f"{n / 1_048_576:.1f} MB"

def suggested_mood():
    """Which music mood fits right now? Uses the Mood Monitor, the Sleep monitor and the time of day."""
    try:
        from zenaut_mood import load_log
        log = load_log()
    except Exception:
        log = []
    if log:
        e = log[-1]
        if e.get("status") == "red" or e.get("stress", 0) >= 4 or e.get("anxiety", 0) >= 4:
            return "Calm", "Your last mood check-in showed high stress or anxiety."
        if e.get("mood", 3) <= 2:
            return "Memories from home", "Your mood was low at the last check-in."
        if e.get("energy", 3) <= 2:
            return "Energize", "Your energy was low at the last check-in."
    try:
        import zenaut_sleep as zs
        d = zs.load()
        plan = zs.current_plan(d)
        n = datetime.now()
        m = n.hour * 60 + n.minute
        to_bed = (plan["bed"] - m) % 1440
        if to_bed <= 120 or (m - plan["bed"]) % 1440 < (plan["wake"] - plan["bed"]) % 1440:
            return "Sleep", f"It is close to your bedtime ({zs.hm(plan['bed'])})."
        last = sorted(d["log"], key=lambda x: x["date"])[-1:] 
        if last and last[0]["dur"] < 360:
            return "Calm", "You slept less than 6 hours last night."
    except Exception:
        pass
    if log:
        return "Focus", "Your last check-in looked steady."
    return None

# ------------------------------------------------------------------ page shell
CSS = """
.wb .tile{background:var(--panel2);border-radius:14px;padding:.8rem .9rem;border-left:5px solid var(--grey)}
.wb .tile span{font-size:.78rem;color:var(--grey)}.wb .tile b{display:block;font-size:1.4rem}
.wb .pick{background:var(--panel2);border:1px solid rgba(174,178,177,.25);border-left:6px solid var(--red);border-radius:14px;padding:.8rem 1rem;margin-top:1rem}
.wb .pill{display:inline-block;padding:.1rem .65rem;border-radius:999px;background:var(--panel2);border:1px solid var(--grey);font-size:.75rem}
.wb .row{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-top:.7rem}
.wb .btn.sm{padding:.25rem .8rem}.wb .btn.danger{border-color:var(--bad);color:var(--bad)}
.wb .song{border-bottom:1px solid rgba(174,178,177,.2);padding:.7rem 0}.wb .song:last-child{border:0}
.wb .song summary{cursor:pointer;font-weight:600}
.wb .checks{display:grid;gap:.3rem;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));max-height:340px;overflow-y:auto;margin-top:.5rem}
.wb .checks label{display:flex;gap:.5rem;align-items:center;color:var(--text);font-size:.9rem}.wb .checks input{width:auto;margin:0}
.wb .player .now{display:flex;gap:14px;align-items:center}
.wb .player .art{width:72px;height:72px;border-radius:16px;background:var(--red);display:grid;place-items:center;font-size:34px;flex:none;color:#fff}
.wb .player .art.spin{animation:zpulse 2.4s ease-in-out infinite}
@keyframes zpulse{50%{transform:scale(1.06)}}
.wb #mt{font-weight:800;font-size:1.15rem;line-height:1.2;overflow-wrap:anywhere}
.wb .player .time{font-size:.78rem;width:40px;color:var(--grey);font-variant-numeric:tabular-nums}
.wb .player input[type=range]{flex:1;min-width:0;padding:0;border:0;background:transparent;accent-color:var(--red)}
.wb .player .ctl{justify-content:center}
.wb .player button{font:inherit;font-weight:700;background:var(--panel2);color:var(--text);border:1px solid var(--grey);border-radius:12px;padding:.5rem .9rem;cursor:pointer;min-width:46px}
.wb .player button.on,.wb .player button.play{background:var(--red);border-color:var(--red);color:#fff}
.wb .player button.play{padding:.5rem 1.4rem;font-size:1.05rem}
.wb .player ol{list-style:none;margin:14px 0 0;padding:0;max-height:240px;overflow-y:auto}
.wb .player li{display:flex;justify-content:space-between;gap:10px;padding:.55rem .7rem;border-radius:10px;cursor:pointer}
.wb .player li:hover{background:var(--panel2)}.wb .player li.cur{background:var(--red);color:#fff;font-weight:700}
.wb .player li span:last-child{opacity:.75;font-size:.8rem;flex:none}
@media (prefers-reduced-motion:reduce){.wb .player .art.spin{animation:none}}
"""

def page(heading, body, active):
    links = "".join(f'<a class="{"on" if k == active else ""}" href="{url_for("music." + k)}">{t}</a>' for k, t in TABS)
    return render_template("wellbeing.html", title="Offline music player", heading=heading, links=Markup(links),
                           body=Markup(f"<style>{CSS}</style>{body}"), css=Markup(WB_CSS), active="music")

def empty_state():
    return (f'<div class="card"><p>Your library is empty. Open <a href="{url_for("music.upload")}">Upload</a> and add your own songs. '
            'They are saved on this device and play offline.</p></div>')

def opts(items, sel=None):
    return "".join(f'<option{" selected" if i == sel else ""}>{escape(i)}</option>' for i in items)

# ------------------------------------------------------------------ player
PLAYER = """
<div class="card player"><h2>Now playing</h2>
  <div class="now"><div class="art" id="mart" aria-hidden="true">&#9835;</div>
    <div><div id="mt">Nothing playing</div><div class="small" id="ma">Choose a song below</div></div></div>
  <div class="row"><span class="time" id="mcur">0:00</span><input type="range" id="mseek" min="0" max="1000" value="0" aria-label="Seek"><span class="time" id="mdur">0:00</span></div>
  <div class="row ctl">
    <button id="mshuf" type="button" aria-label="Shuffle">Shuffle</button><button id="mprev" type="button" aria-label="Previous">&#9198;</button>
    <button id="mpp" class="play" type="button" aria-label="Play">&#9654;</button><button id="mnext" type="button" aria-label="Next">&#9197;</button>
    <button id="mrep" type="button" aria-label="Repeat">Repeat</button></div>
  <div class="row"><span class="small">Volume</span><input type="range" id="mvol" min="0" max="100" value="80" aria-label="Volume"></div>
  <div class="row"><span class="small">Sleep timer</span>
    <select id="msleep" style="width:auto"><option value="0">Off</option><option value="15">15 min</option><option value="30">30 min</option><option value="45">45 min</option><option value="60">60 min</option></select>
    <span class="small" id="msleepinfo"></span></div>
  <ol id="mlist"></ol><audio id="mau" preload="metadata"></audio></div>
<script>
(function(){
const T=__TRACKS__;let i=0,shuffle=false,repeat=false,timer=null;
const au=document.getElementById("mau"),$=id=>document.getElementById(id);
const fmt=s=>isFinite(s)?Math.floor(s/60)+":"+String(Math.floor(s%60)).padStart(2,"0"):"0:00";
const esc=s=>String(s).replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
function render(){
  $("mlist").innerHTML=T.map((t,k)=>`<li data-k="${k}" class="${k===i?"cur":""}"><span>${esc(t.title)} <small>${esc(t.artist)}</small></span><span>${esc(t.mood)}</span></li>`).join("");
  if(T[i]){$("mt").textContent=T[i].title;$("ma").textContent=T[i].artist+" | "+T[i].mood}
  $("mshuf").classList.toggle("on",shuffle);$("mrep").classList.toggle("on",repeat);
}
function load(k,play){if(!T.length)return;i=(k+T.length)%T.length;au.src=T[i].src;render();if(play)au.play().catch(()=>{})}
function nextIdx(){if(shuffle&&T.length>1){let k;do{k=Math.floor(Math.random()*T.length)}while(k===i);return k}return i+1}
$("mpp").onclick=()=>{if(!au.src)load(i,true);else au.paused?au.play():au.pause()};
$("mnext").onclick=()=>load(nextIdx(),true);
$("mprev").onclick=()=>au.currentTime>4?(au.currentTime=0):load(i-1,true);
$("mshuf").onclick=()=>{shuffle=!shuffle;render()};$("mrep").onclick=()=>{repeat=!repeat;render()};
$("mlist").onclick=e=>{const li=e.target.closest("li");if(li)load(+li.dataset.k,true)};
au.onplay=()=>{$("mpp").innerHTML="&#10074;&#10074;";$("mart").classList.add("spin");
  if(T[i]&&T[i].played){fetch(T[i].played,{method:"POST"}).catch(()=>{})}};
au.onpause=()=>{$("mpp").innerHTML="&#9654;";$("mart").classList.remove("spin")};
au.onended=()=>{if(repeat&&T.length===1){au.currentTime=0;au.play()}else if(i+1>=T.length&&!repeat&&!shuffle){au.pause()}else load(nextIdx(),true)};
au.ontimeupdate=()=>{$("mcur").textContent=fmt(au.currentTime);$("mdur").textContent=fmt(au.duration);if(au.duration)$("mseek").value=au.currentTime/au.duration*1000};
$("mseek").oninput=()=>{if(au.duration)au.currentTime=$("mseek").value/1000*au.duration};
$("mvol").oninput=()=>au.volume=$("mvol").value/100;au.volume=.8;
$("msleep").onchange=()=>{clearTimeout(timer);const m=+$("msleep").value;$("msleepinfo").textContent=m?"Fades out in "+m+" min":"";if(!m)return;
  timer=setTimeout(()=>{const v0=au.volume;let n=20;const f=setInterval(()=>{au.volume=Math.max(0,v0*(--n/20));
    if(n<=0){clearInterval(f);au.pause();au.volume=v0;$("msleepinfo").textContent="Stopped by sleep timer"}},500)},m*60000)};
render();if(T.length)au.src=T[0].src;
})();
</script>"""

@music.route("/")
def player():
    db = load_db()
    if not db["tracks"]:
        return page("Music", empty_state(), "player")
    sugg = suggested_mood()
    out = ""
    if sugg:
        mood, why = sugg
        btn = ""
        if queue_for(db, f"Mood: {mood}"):
            btn = f'<div class="row"><a class="btn primary" href="{url_for("music.player", q="Mood: " + mood)}">Play {escape(mood)} music</a></div>'
        else:
            btn = f'<p class="small">You have no songs tagged {escape(mood)} yet. Tag some in the Library.</p>'
        out += f'<div class="pick"><b>Suggested for you: {escape(mood)}</b><br>{escape(why)}{btn}</div>'
    options = queue_options(db)
    key = request.args.get("q", "All songs")
    key = key if key in options else "All songs"
    sel = (f'<div class="card"><form method="get" class="row" style="margin-top:0"><label style="flex:1;min-width:200px">Play from<select name="q" onchange="this.form.submit()">'
           f'{opts(options, key)}</select></label><noscript><button class="btn">Go</button></noscript></form></div>')
    queue = queue_for(db, key)
    if not queue:
        return page("Music", out + sel + '<div class="card"><p>Nothing in this list yet.</p></div>', "player")
    items = [dict(title=t["title"], artist=t["artist"], mood=t["mood"], src=url_for("music.audio", tid=t["id"]),
                  played=url_for("music.played", tid=t["id"])) for t in queue if (FILES / f"{t['id']}.{t['ext']}").exists()]
    total = sum(t["size"] for t in queue)
    js = PLAYER.replace("__TRACKS__", json.dumps(items).replace("</", "<\\/"))
    foot = f'<p class="small" style="margin-top:.8rem">{len(items)} songs, {fmt_size(total)}. Playing from local storage, no internet needed.</p>'
    return page("Music", out + sel + js + foot, "player")

@music.route("/audio/<tid>")
def audio(tid):
    t = find(load_db(), tid)
    path = FILES / f"{tid}.{t['ext']}" if t else None
    if not t or not path.exists():
        abort(404)
    return send_file(path, mimetype=MIME[t["ext"]], conditional=True)

@music.route("/played/<tid>", methods=["POST"])
def played(tid):
    db = load_db()
    t = find(db, tid)
    if t:
        t["plays"] = t.get("plays", 0) + 1
        save_db(db)
    return jsonify(ok=True)

# ------------------------------------------------------------------ library
@music.route("/library")
def library():
    db = load_db()
    if not db["tracks"]:
        return page("Music library", empty_state(), "library")
    q, mood = request.args.get("q", "").strip(), request.args.get("mood", "All")
    total = sum(t["size"] for t in db["tracks"])
    tiles = ('<div class="grid" style="margin-top:1rem">'
             f'<div class="tile"><span>Songs</span><b>{len(db["tracks"])}</b></div>'
             f'<div class="tile"><span>Favorites</span><b>{sum(1 for t in db["tracks"] if t["favorite"])}</b></div>'
             f'<div class="tile"><span>Stored on this device</span><b>{fmt_size(total)}</b></div></div>')
    shown = [t for t in db["tracks"] if (mood == "All" or t["mood"] == mood)
             and (not q or q.lower() in t["title"].lower() or q.lower() in t["artist"].lower())]
    flt = ('<div class="card"><form method="get" class="log">'
           f'<label>Search title or artist<input name="q" value="{escape(q)}"></label>'
           f'<label>Mood<select name="mood">{opts(["All"] + MOODS, mood)}</select></label>'
           '<div style="align-self:end"><button class="btn primary">Filter</button></div></form>'
           f'<p class="small" style="margin-top:.5rem">{len(shown)} songs</p></div>')
    rows = ""
    for t in sorted(shown, key=lambda x: x["title"].lower()):
        star = "★ " if t["favorite"] else ""
        act = url_for("music.edit", tid=t["id"])
        rows += (f'<details class="song"><summary>{star}{escape(t["title"])} &nbsp;|&nbsp; {escape(t["artist"])} &nbsp;<span class="pill">{escape(t["mood"])}</span></summary>'
                 f'<form method="post" action="{act}" class="log" style="margin-top:.6rem">'
                 f'<label>Title<input name="title" value="{escape(t["title"])}" maxlength="120"></label>'
                 f'<label>Artist<input name="artist" value="{escape(t["artist"])}" maxlength="80"></label>'
                 f'<label>Mood<select name="mood">{opts(MOODS, t["mood"])}</select></label>'
                 '<div style="align-self:end;display:flex;gap:.4rem;flex-wrap:wrap">'
                 '<button class="btn primary sm" name="do" value="save">Save changes</button>'
                 f'<button class="btn sm" name="do" value="fav">{"Remove favorite" if t["favorite"] else "Favorite"}</button>'
                 '<button class="btn sm danger" name="do" value="delete" onclick="return confirm(\'Delete this song from the device?\')">Delete</button></div></form>'
                 f'<p class="small">{t["ext"].upper()} | {fmt_size(t["size"])} | added {escape(t["added"])} | played {t.get("plays", 0)} times</p></details>')
    return page("Music library", tiles + flt + f'<div class="card">{rows or "<p>No songs match.</p>"}</div>', "library")

@music.route("/edit/<tid>", methods=["POST"])
def edit(tid):
    db = load_db()
    t = find(db, tid)
    if t:
        do = request.form.get("do")
        if do == "save":
            mood = request.form.get("mood")
            t.update(title=request.form.get("title", "").strip()[:120] or t["title"],
                     artist=request.form.get("artist", "").strip()[:80] or "Unknown artist",
                     mood=mood if mood in MOODS else t["mood"])
        elif do == "fav":
            t["favorite"] = not t["favorite"]
        elif do == "delete":
            delete_track(db, tid)
        save_db(db)
    return redirect(url_for("music.library"))

# ------------------------------------------------------------------ playlists
@music.route("/playlists")
def playlists():
    db = load_db()
    create = (f'<div class="card"><h2>New playlist</h2><form method="post" action="{url_for("music.playlist_create")}" class="log">'
              '<label>Name<input name="name" maxlength="60" required></label><div style="align-self:end"><button class="btn primary">Create playlist</button></div></form></div>')
    if not db["playlists"]:
        return page("Playlists", create + '<div class="card"><p>No playlists yet. Create one above, then add songs.</p></div>', "playlists")
    names = list(db["playlists"])
    pl = request.args.get("pl", names[0])
    pl = pl if pl in db["playlists"] else names[0]
    pick = ('<div class="card"><form method="get" class="log"><label>Edit playlist'
            f'<select name="pl" onchange="this.form.submit()">{opts(names, pl)}</select></label></form>')
    inside = set(db["playlists"][pl])
    checks = "".join(f'<label><input type="checkbox" name="songs" value="{t["id"]}"{" checked" if t["id"] in inside else ""}>'
                     f'{escape(t["title"])} | {escape(t["artist"])}</label>' for t in sorted(db["tracks"], key=lambda x: x["title"].lower()))
    form = (f'<form method="post" action="{url_for("music.playlist_save")}"><input type="hidden" name="name" value="{escape(pl)}">'
            f'<h2 style="margin-top:1rem">Songs in "{escape(pl)}"</h2>' + (f'<div class="checks">{checks}</div>' if checks else '<p>Upload songs first.</p>') +
            '<div class="row"><button class="btn primary" name="do" value="save">Save playlist</button>'
            f'<button class="btn danger" name="do" value="delete" onclick="return confirm(\'Delete this playlist? Your songs stay.\')">Delete playlist</button>'
            f'<a class="btn" href="{url_for("music.player", q="Playlist: " + pl)}">Play it</a></div></form></div>')
    return page("Playlists", create + pick + form, "playlists")

@music.route("/playlists/create", methods=["POST"])
def playlist_create():
    db = load_db()
    name = request.form.get("name", "").strip()[:60]
    if name:
        db["playlists"].setdefault(name, [])
        save_db(db)
    return redirect(url_for("music.playlists", pl=name))

@music.route("/playlists/save", methods=["POST"])
def playlist_save():
    db = load_db()
    name = request.form.get("name", "")
    if name in db["playlists"]:
        if request.form.get("do") == "delete":
            del db["playlists"][name]
        else:
            valid = {t["id"] for t in db["tracks"]}
            db["playlists"][name] = [i for i in request.form.getlist("songs") if i in valid]
        save_db(db)
    return redirect(url_for("music.playlists", pl=name))

# ------------------------------------------------------------------ upload
@music.route("/upload", methods=["GET", "POST"])
def upload():
    db = load_db()
    msg = ""
    if request.method == "POST":
        mood = request.form.get("mood")
        mood = mood if mood in MOODS else MOODS[0]
        added = skipped = 0
        for f in request.files.getlist("songs"):
            if not f.filename:
                continue
            if add_track(db, f.filename, f.read(), mood, request.form.get("artist", "")):
                added += 1
            else:
                skipped += 1
        save_db(db)
        msg = (f'<div class="alert green"><b>Added {added} song(s).</b>'
               + (f" Skipped {skipped} duplicate or unsupported file(s)." if skipped else "") + "</div>")
    form = (f'<div class="card"><h2>Upload your music</h2><p class="small">Choose songs from your device. They are copied into ZENAUT and stay available offline.</p>'
            '<form method="post" enctype="multipart/form-data" class="log">'
            f'<label>Mood for these songs<select name="mood">{opts(MOODS)}</select></label>'
            '<label>Artist (optional)<input name="artist" maxlength="80"></label>'
            f'<label style="grid-column:1/-1">Songs ({", ".join(ALLOWED)})<input type="file" name="songs" multiple accept="{",".join("." + e for e in ALLOWED)}" required></label>'
            '<div><button class="btn primary">Add to library</button></div></form>' + msg +
            '<p class="small" style="margin-top:.6rem">Tip: rename a song or change its mood later in Library.</p></div>')
    return page("Upload music", form, "upload")
