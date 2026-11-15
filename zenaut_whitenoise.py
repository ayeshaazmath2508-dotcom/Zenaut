"""ZENAUT · White noise, its own page on the dashboard (Flask blueprint).

Your sound engine is unchanged: white, pink and brown noise are generated in Python (numpy)
and served as a looping WAV, so it works fully offline. Needs numpy (see requirements.txt).
"""
import io
import wave
from functools import lru_cache

import numpy as np
from flask import Blueprint, render_template, send_file, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

whitenoise = Blueprint("whitenoise", __name__, url_prefix="/whitenoise")
SR = 22050  # sample rate
KINDS = ("white", "pink", "brown")


# ------------------------------- sound engine (Python) -------------------------------
def _colored_noise(n: int, power: float) -> np.ndarray:
    """power 0 = white, 1 = pink, 2 = brown (shapes random noise by frequency)."""
    spectrum = np.fft.rfft(np.random.randn(n))
    freqs = np.fft.rfftfreq(n)
    freqs[0] = freqs[1]
    spectrum /= freqs ** (power / 2)
    x = np.fft.irfft(spectrum, n)
    return x / np.max(np.abs(x))


@lru_cache(maxsize=8)
def noise_wav(kind: str, seconds: int = 10) -> bytes:
    power = {"white": 0, "pink": 1, "brown": 2}[kind]
    data = _colored_noise(SR * seconds, power) * 0.5
    buf = io.BytesIO()
    with wave.open(buf, "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(SR)
        f.writeframes((np.clip(data, -1, 1) * 32767).astype(np.int16).tobytes())
    return buf.getvalue()


# ------------------------------- page -------------------------------
CSS = """
.wb .wn{max-width:520px;margin:1rem auto 0}
.wb .wn-dial{display:grid;place-items:center;margin:.4rem 0 1rem}
.wb .wn-ring{width:140px;height:140px;border-radius:50%;display:grid;place-items:center;font-size:2.6rem;
  border:3px solid var(--blue);box-shadow:0 0 0 6px rgba(11,61,145,.35);background:var(--space)}
.wb .wn-ring.on{border-color:var(--red);box-shadow:0 0 0 6px rgba(252,61,33,.22),0 0 40px rgba(252,61,33,.3);animation:wnpulse 3s ease-in-out infinite}
@keyframes wnpulse{50%{box-shadow:0 0 0 12px rgba(252,61,33,.1),0 0 60px rgba(252,61,33,.18)}}
.wb .wn-status{display:flex;justify-content:center;margin-bottom:1rem}
.wb .wn-pill{font-size:.8rem;padding:.25rem .8rem;border-radius:999px;border:1px solid currentColor;font-weight:700}
.wb .wn-pill.safe{color:var(--ok)}.wb .wn-pill.watch{color:var(--warn)}
.wb .wn-chips{display:flex;gap:.5rem;flex-wrap:wrap;justify-content:center;margin-bottom:1rem}
.wb .wn-chip{padding:.55rem 1.2rem;border-radius:999px;border:1px solid var(--grey);background:transparent;color:var(--text);font:inherit;cursor:pointer}
.wb .wn-chip[aria-pressed=true]{background:var(--blue);border-color:var(--red);color:#fff;font-weight:700}
.wb .wn label{margin-top:1rem}.wb .wn label b{float:right;color:var(--text)}
.wb .wn input[type=range]{padding:0;border:0;background:transparent;accent-color:var(--red)}
.wb .wn-go{width:100%;margin-top:1rem;padding:1rem;border:0;border-radius:10px;cursor:pointer;background:var(--red);color:#fff;font:inherit;font-size:1.05rem;font-weight:700;letter-spacing:.05em}
.wb .wn-go.playing{background:var(--blue)}
.wb .wn .small{text-align:center;margin:1rem 0 0}
@media (prefers-reduced-motion:reduce){.wb .wn-ring.on{animation:none}}
"""

BODY = """
<style>__CSS__</style>
<div class="card wn">
  <div class="wn-dial"><div class="wn-ring" id="wn-ring">🌌</div></div>
  <div class="wn-status"><span class="wn-pill safe" id="wn-pill">● Safe · Ready</span></div>
  <div class="wn-chips" id="wn-chips">
    <button class="wn-chip" data-kind="white" aria-pressed="true">White</button>
    <button class="wn-chip" data-kind="pink" aria-pressed="false">Pink</button>
    <button class="wn-chip" data-kind="brown" aria-pressed="false">Brown</button>
  </div>
  <label for="wn-timer">Sleep timer</label>
  <select id="wn-timer">
    <option value="0">Off</option><option value="15">15 minutes</option>
    <option value="30">30 minutes</option><option value="60">60 minutes</option>
  </select>
  <label for="wn-vol">Volume <b id="wn-vv">50%</b></label>
  <input id="wn-vol" type="range" min="0" max="1" step="0.01" value="0.5">
  <button id="wn-go" class="wn-go" type="button">▶ PLAY</button>
  <audio id="wn-au" loop></audio>
  <p class="small" id="wn-note">Works fully offline. Sound stops if you leave this page.</p>
</div>
<script>
(function () {
  var $ = function (id) { return document.getElementById(id); };
  var au = $("wn-au"), kind = "white", timer = null, base = "__AUDIO__";
  $("wn-chips").onclick = function (e) {
    var c = e.target.closest(".wn-chip"); if (!c) return;
    kind = c.dataset.kind;
    document.querySelectorAll(".wn-chip").forEach(function (x) { x.setAttribute("aria-pressed", x === c); });
    if (!au.paused) start();
  };
  $("wn-vol").oninput = function () { au.volume = +$("wn-vol").value; $("wn-vv").textContent = Math.round($("wn-vol").value * 100) + "%"; };
  async function start() {
    clearTimeout(timer);
    au.src = base + kind + ".wav";
    au.volume = +$("wn-vol").value;
    try { await au.play(); } catch (e) { $("wn-note").textContent = "Sound blocked: " + e.message; return; }
    $("wn-go").textContent = "■ STOP"; $("wn-go").classList.add("playing");
    $("wn-ring").classList.add("on");
    $("wn-pill").className = "wn-pill watch"; $("wn-pill").textContent = "● Monitoring · Playing " + kind;
    var m = +$("wn-timer").value;
    if (m) timer = setTimeout(stop, m * 60000);
  }
  function stop() {
    clearTimeout(timer); au.pause();
    $("wn-go").textContent = "▶ PLAY"; $("wn-go").classList.remove("playing");
    $("wn-ring").classList.remove("on");
    $("wn-pill").className = "wn-pill safe"; $("wn-pill").textContent = "● Safe · Ready";
  }
  $("wn-go").onclick = function () { au.paused ? start() : stop(); };
})();
</script>
"""


@whitenoise.route("/")
def page():
    body = BODY.replace("__CSS__", CSS).replace("__AUDIO__", url_for("whitenoise.audio", kind="white")[:-len("white.wav")])
    return render_template("wellbeing.html", title="Steady background sound for focus and sleep", links="",
                           body=Markup(body), css=Markup(WB_CSS), heading="White noise", active="whitenoise")


@whitenoise.route("/audio/<kind>.wav")
def audio(kind):
    if kind not in KINDS:
        return "unknown noise", 404
    # send_file supports Range requests, which Safari needs to play audio
    return send_file(io.BytesIO(noise_wav(kind)), mimetype="audio/wav", conditional=True)
