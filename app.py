import os

from flask import Flask, abort, redirect, render_template, url_for

from zenaut_reports import reports
from zenaut_stress import stress
from zenaut_mood import mood
from zenaut_physical import physical
from zenaut_whitenoise import whitenoise
from zenaut_games import games
from zenaut_astrocare import astro
from zenaut_cosmic import cosmic
from zenaut_sleep import sleepcare
from zenaut_music import music
from zenaut_books import books
from zenaut_earth import earthlink, start_background_sync
from zenaut_physical import overall_status

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 1024 * 1024 * 1024  # allow big song uploads (1 GB per upload)
app.register_blueprint(reports)
app.register_blueprint(cosmic)  # Cosmic Journal: separate full-screen world (zenaut_cosmic.py)
app.register_blueprint(sleepcare)  # Sleep & circadian centre (zenaut_sleep.py)
app.register_blueprint(music)  # Music player (zenaut_music.py)
app.register_blueprint(books)  # Books: your offline library with upload (zenaut_books.py)
app.register_blueprint(earthlink)  # Connection with Earth > Messages from home (zenaut_earth.py)
app.register_blueprint(astro)  # Grounding, guided routines, Mission & emergency (zenaut_astrocare.py)
app.register_blueprint(games)  # Games page (zenaut_games.py)
app.register_blueprint(whitenoise)  # White noise page (zenaut_whitenoise.py)
app.register_blueprint(physical)  # Physical health page (zenaut_physical.py)
app.register_blueprint(mood)  # Mood Monitor page (zenaut_mood.py)
app.register_blueprint(stress)  # Mission Stress Check tab (zenaut_stress.py)  # Your wellbeing: daily graphs and weekly report (zenaut_reports.py)

# Every dashboard component lives in this list.
# "slug" becomes the page address (for example /journal).
# To build a component later, replace its placeholder page with its own template.
MODULES = [
    {"slug": "journal",    "name": "Journal",                 "icon": "✎", "blurb": "Write down your day. Entries stay on this device."},
    {"slug": "profile",    "name": "Profile",                 "icon": "☺", "blurb": "Your details, mission and medical notes."},
    {"slug": "physical",   "name": "Physical health",         "icon": "♥", "blurb": "One tab per reading: enter, track and see alerts."},
    {"slug": "sleep",      "name": "Sleep",                   "icon": "☾", "blurb": "Circadian calculator, sleep monitor, smart light, reminders and relaxation."},
    {"slug": "music",      "name": "Music",                   "icon": "♫", "blurb": "Your offline library: playlists matched to your mood, sleep timer."},
    {"slug": "whitenoise", "name": "White noise",             "icon": "≈", "blurb": "Steady background sound for focus and sleep."},
    {"slug": "earth",      "name": "Connection with Earth",   "icon": "◍", "blurb": "Messages, photos, videos and notes from your loved ones, plus mission and emergency tools."},
    {"slug": "books",      "name": "Books",                   "icon": "▤", "blurb": "Your offline library. Upload books and read them with no internet."},
    {"slug": "games",      "name": "Games",                  "icon": "♟", "blurb": "Crew game night: trivia, word games, Ludo, chess and more."},
    {"slug": "grounding",  "name": "Grounding and exercises", "icon": "❍", "blurb": "Breathing, grounding and relaxation exercises."},
    {"slug": "mood",       "name": "Mood Monitor",           "icon": "◐", "blurb": "Daily mood, energy and sleep check-in with trends."},
    {"slug": "wellbeing",  "name": "Your wellbeing",          "icon": "✚", "blurb": "Daily graphs, weekly report and your health scores."},
]

# Header chip: built from your latest Physical health readings (green / orange / red).
# Falls back to this default if there are no readings yet.
STATUS = {"level": "ok", "label": "No readings yet"}


@app.context_processor
def shell():
    try:
        status = overall_status()
    except Exception:
        status = STATUS
    return {"modules": MODULES, "status": status}


@app.route("/")
def dashboard():
    return render_template("dashboard.html", active="")


@app.route("/music")
def music_page():
    return redirect(url_for("music.player"))


@app.route("/games")
def games_page():
    return redirect(url_for("games.page"))


@app.route("/whitenoise")
def whitenoise_page():
    return redirect(url_for("whitenoise.page"))


@app.route("/physical")
def physical_page():
    return redirect(url_for("physical.dashboard"))


@app.route("/mood")
def mood_page():
    return redirect(url_for("mood.dashboard"))


@app.route("/sleep")
def sleep_page():
    return redirect(url_for("sleepcare.overview"))


@app.route("/journal")
def journal_page():
    return redirect(url_for("cosmic.page"))


@app.route("/grounding")
def grounding_page():
    return redirect(url_for("astro.relax"))


@app.route("/earth")
def earth_page():
    return redirect(url_for("earthlink.home"))


@app.route("/books")
def books_page():
    return redirect(url_for("books.library"))


@app.route("/wellbeing")
def wellbeing():
    return redirect(url_for("reports.dashboard"))


@app.route("/profile")
def profile():
    return render_template("profile.html", active="profile")


@app.route("/<slug>")
def module(slug):
    item = next((m for m in MODULES if m["slug"] == slug), None)
    if item is None:
        abort(404)
    return render_template("module.html", item=item, active=slug)


@app.errorhandler(404)
def not_found(_):
    return render_template("module.html", item=None, active=""), 404


if __name__ == "__main__":
    # Start the background check for new messages from home once (the debug reloader runs this file twice).
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_background_sync()
    app.run(debug=True, port=5000)  # debug=True reloads when you save a file
