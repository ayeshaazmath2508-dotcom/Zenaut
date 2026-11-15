"""ZENAUT · Games, its own page on the dashboard (Flask blueprint).

Ported from your terminal game suite (space_games.py): Space Trivia, Word Scramble, Riddles,
Reaction Race, Rock Paper Scissors, Heads Up, Imposter, Ludo, Chess, Orbit Runner and Memory Match.
Crew members take turns on one shared screen. The games run in the browser (static/js/games.js),
so they work offline. Scores last while the page is open; crew names are remembered.
"""
from pathlib import Path

from flask import Blueprint, render_template, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

games = Blueprint("games", __name__, url_prefix="/games")
JS = Path(__file__).parent / "static" / "js" / "games.js"

CSS = """
.wb .gm-game{display:flex;flex-direction:column;gap:.15rem;text-align:left;padding:.7rem .9rem;border-radius:12px;border:1px solid rgba(174,178,177,.35);background:var(--panel2);color:var(--text);font:inherit;cursor:pointer}
.wb .gm-game:hover{border-color:var(--red)}.wb .gm-game b{font-size:.95rem}.wb .gm-game span{font-size:.78rem;color:var(--grey)}
.wb #gm-menu{display:grid;grid-template-columns:repeat(auto-fill,minmax(190px,1fr));gap:.6rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
.wb .row input{flex:1;min-width:10rem;width:auto}
.wb .gm-line{padding:.3rem 0;font-size:.95rem}.wb .gm-line.ok{color:var(--ok);font-weight:700}.wb .gm-line.bad{color:var(--warn)}
.wb .gm-line.gm-echo{color:var(--grey);font-size:.85rem}.wb .gm-big{font-size:1.25rem;font-weight:700}
.wb #gm-log{max-height:16rem;overflow-y:auto;border-bottom:1px solid rgba(174,178,177,.2);margin-bottom:.6rem}
.wb #gm-log:empty{display:none}
.wb .gm-ask label{display:block;color:var(--text);font-size:1rem;margin-bottom:.2rem}
.wb .gm-opts{display:flex;gap:.5rem;flex-wrap:wrap}.wb .gm-left{font-size:.85rem;color:var(--warn);font-weight:700}
.wb .gm-wait p,.wb .gm-choose p{margin:.2rem 0 .5rem}
.wb .gm-secret{padding:1.4rem;border-radius:14px;background:var(--blue);color:#fff;font-size:1.4rem;font-weight:700;text-align:center;margin:.6rem 0}
.wb .gm-react{width:100%;min-height:9rem;border:0;border-radius:16px;font:inherit;font-size:1.6rem;font-weight:800;cursor:pointer;color:#fff}
.wb .gm-react.wait{background:var(--bad)}.wb .gm-react.go{background:var(--ok);color:#07102A}
.wb .gm-chess{display:grid;grid-template-columns:repeat(8,1fr);max-width:480px;border:2px solid var(--blue)}
.wb .gm-sq{aspect-ratio:1;border:0;padding:0;font-size:clamp(1.4rem,6vw,2.4rem);line-height:1;cursor:pointer;position:relative;font-family:"Segoe UI Symbol","Apple Symbols","Noto Sans Symbols 2",serif}
.wb .gm-sq.lt{background:var(--panel2)}.wb .gm-sq.dk{background:var(--blue)}
.wb .gm-sq.pw{color:#fff;text-shadow:0 0 2px #000,0 0 3px #000,0 0 3px #000}
.wb .gm-sq.pb{color:#10142a;text-shadow:0 0 2px #fff,0 0 3px #fff}
.wb .gm-sq.sel{outline:3px solid var(--red);outline-offset:-3px}
.wb .gm-sq.tgt::after{content:"";position:absolute;inset:34%;border-radius:50%;background:var(--red);opacity:.85}
.wb .gm-mem{display:grid;grid-template-columns:repeat(4,1fr);gap:.5rem;max-width:360px}
.wb .gm-card{aspect-ratio:1;border:2px solid rgba(174,178,177,.4);border-radius:12px;background:var(--blue);color:#fff;font-size:2rem;cursor:pointer}
.wb .gm-card.up{background:var(--panel2);border-color:var(--red)}.wb .gm-card.done{background:var(--panel2);border-color:var(--ok)}
.wb #gm-cv{max-width:100%;height:auto;border-radius:14px;display:block;background:#0a0f24}
.wb .gm-score table{width:100%}
@media (prefers-reduced-motion:reduce){.wb *{scroll-behavior:auto}}
"""

BODY = """
<style>__CSS__</style>
<div class="gm">
  <div class="card"><h2>Crew</h2>
    <label for="gm-crew">Names, separated by commas (up to 8)</label>
    <div class="row"><input id="gm-crew" maxlength="200" autocomplete="off"><button type="button" class="btn primary" id="gm-save">Save crew</button></div>
    <p class="small" id="gm-crewmsg"></p></div>
  <div class="card"><h2>Pick a game</h2><div id="gm-menu"></div></div>
  <div class="card" id="gm-stagecard" hidden><h2 id="gm-title"></h2>
    <div id="gm-log" aria-live="polite"></div><div id="gm-act"></div>
    <div class="row"><button type="button" class="btn" id="gm-stop">Stop game</button></div></div>
  <div class="card gm-score"><h2>Scoreboard</h2><div id="gm-score"></div>
    <div class="row"><button type="button" class="btn" id="gm-reset">Reset scores</button></div></div>
</div>
<script src="__JS__"></script>
"""


@games.route("/")
def page():
    src = url_for("static", filename="js/games.js", v=int(JS.stat().st_mtime))
    body = BODY.replace("__CSS__", CSS).replace("__JS__", src)
    return render_template("wellbeing.html", title="Crew game night · works offline", links="", body=Markup(body),
                           css=Markup(WB_CSS), heading="Games", active="games")
