/* ZENAUT · Games: crew game night, ported from the terminal version (space_games.py).
   Turn-taking on one shared screen, works offline. Logic is plain JavaScript, no libraries. */
(function () {
"use strict";

/* ====================== pure logic (also tested in Node) ====================== */
const TRIVIA = [
  ["Which planet has the most moons?", "saturn"], ["What is the closest star to our Sun?", "proxima centauri"],
  ["How many planets are in our solar system?", "8"], ["Which planet is known as the Red Planet?", "mars"],
  ["What galaxy do we live in?", "milky way"], ["Who was the first human in space?", "yuri gagarin"],
  ["What is the largest planet in our solar system?", "jupiter"], ["What force keeps planets in orbit?", "gravity"],
];
const WORDS = ["rocket", "orbit", "galaxy", "comet", "nebula", "gravity", "asteroid", "eclipse", "meteor", "cosmos", "satellite", "telescope"];
const RIDDLES = [
  ["I have cities but no houses, mountains but no trees, water but no fish. What am I?", "map"],
  ["What gets wetter the more it dries?", "towel"],
  ["The more you take, the more you leave behind. What am I?", "footsteps"],
];
const HEADS = ["Astronaut", "Black Hole", "Zero Gravity", "Space Suit", "Mars Rover", "Moon Landing", "Rocket Launch", "Space Walk",
  "Supernova", "Meteor Shower", "Telescope", "Alien", "Freeze-Dried Ice Cream", "Space Station", "Comet", "Satellite", "Re-entry", "Solar Flare"];
const SPY_WORDS = ["Space Station", "Black Hole", "Moon", "Rocket", "Mars", "Helmet", "Satellite", "Comet", "Telescope", "Space Suit", "Sun", "Saturn"];
const MEMORY_ICONS = ["🚀", "🌍", "🌙", "⭐", "☄️", "🪐", "👽", "🛰️"];
const CFG = { headsSeconds: 60 };

const shuffle = a => { a = a.slice(); for (let i = a.length - 1; i > 0; i--) { const j = Math.floor(Math.random() * (i + 1)); [a[i], a[j]] = [a[j], a[i]]; } return a; };
const sample = (a, n) => shuffle(a).slice(0, n);
const pick = a => a[Math.floor(Math.random() * a.length)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
function scramble(w) { let s = w; for (let i = 0; i < 20 && s === w; i++) s = shuffle(w.split("")).join(""); return s; }

/* chess: 64 squares, index 0 = a8. Uppercase = white. No check rules: capture the king to win. */
const PIECE = { k: "king", q: "queen", r: "rook", b: "bishop", n: "knight", p: "pawn" };
const GLYPH = { k: "♚", q: "♛", r: "♜", b: "♝", n: "♞", p: "♟" };
const isWhite = p => p !== " " && p === p.toUpperCase();
const newBoard = () => ("rnbqkbnrpppppppp" + " ".repeat(32) + "PPPPPPPPRNBQKBNR").split("");
function chessMoves(b, i) {
  const p = b[i], w = isWhite(p), r = Math.floor(i / 8), c = i % 8, out = [], q = p.toLowerCase();
  const add = (R, C) => {
    if (R < 0 || R > 7 || C < 0 || C > 7) return false;
    const s = R * 8 + C;
    if (b[s] !== " " && isWhite(b[s]) === w) return false;
    out.push(s);
    return b[s] === " ";
  };
  const slide = ds => { for (const [dr, dc] of ds) { let k = 1; while (add(r + dr * k, c + dc * k)) k++; } };
  const O = [[1, 0], [-1, 0], [0, 1], [0, -1]], D = [[1, 1], [1, -1], [-1, 1], [-1, -1]];
  if (q === "r") slide(O);
  else if (q === "b") slide(D);
  else if (q === "q") slide(O.concat(D));
  else if (q === "k") { for (const [dr, dc] of O.concat(D)) add(r + dr, c + dc); }
  else if (q === "n") { for (const [dr, dc] of [[2, 1], [2, -1], [-2, 1], [-2, -1], [1, 2], [1, -2], [-1, 2], [-1, -2]]) add(r + dr, c + dc); }
  else {
    const d = w ? -1 : 1, nr = r + d;
    if (nr >= 0 && nr < 8) {
      if (b[nr * 8 + c] === " ") {
        out.push(nr * 8 + c);
        if (r === (w ? 6 : 1) && b[(nr + d) * 8 + c] === " ") out.push((nr + d) * 8 + c);
      }
      for (const dc of [-1, 1]) {
        if (c + dc >= 0 && c + dc < 8) { const s = nr * 8 + c + dc; if (b[s] !== " " && isWhite(b[s]) !== w) out.push(s); }
      }
    }
  }
  return out;
}

/* ludo: pos -1 = base, 0-50 track, 51-56 home column, 57 = home */
const LUDO_START = [0, 26, 13, 39];
const absn = (i, p) => (p >= 0 && p <= 50 ? (LUDO_START[i] + p) % 52 : null);
function ludoMoves(posT, roll) { const m = []; for (let k = 0; k < 4; k++) if (posT[k] < 0 ? roll === 6 : posT[k] + roll <= 57) m.push(k); return m; }
function ludoMove(pos, t, k, roll) {
  pos[t][k] = pos[t][k] < 0 ? 0 : pos[t][k] + roll;
  const a = absn(t, pos[t][k]), captured = [];
  if (a !== null && !LUDO_START.includes(a)) {
    for (let o = 0; o < pos.length; o++) for (let j = 0; j < 4; j++) if (o !== t && absn(o, pos[o][j]) === a) { pos[o][j] = -1; captured.push(o); }
  }
  return captured;
}

if (typeof document === "undefined") {   // Node (tests): export the logic and stop
  module.exports = { TRIVIA, WORDS, scramble, newBoard, chessMoves, ludoMoves, ludoMove, absn, LUDO_START, isWhite, shuffle, sample };
  return;
}

/* ====================== state and small helpers ====================== */
const $ = id => document.getElementById(id);
let players = [], scores = {}, token = 0, cleanups = [];
class Cancel extends Error {}
const KEY = "zenaut_crew";

function add(p, n) { scores[p] = (scores[p] || 0) + n; renderScores(); }
function renderScores() {
  const rows = players.slice().sort((a, b) => (scores[b] || 0) - (scores[a] || 0))
    .map(p => `<tr><td>${esc(p)}</td><td style="text-align:right"><b>${scores[p] || 0}</b></td></tr>`).join("");
  $("gm-score").innerHTML = `<table>${rows}</table>`;
}
function stopAll() { cleanups.forEach(f => { try { f(); } catch (e) {} }); cleanups = []; }

/* ====================== UI primitives handed to each game ====================== */
function makeUI(tok) {
  const log = $("gm-log"), act = $("gm-act");
  const chk = () => { if (tok !== token) throw new Cancel(); };
  const ui = {
    alive: () => tok === token,
    cleanup: f => cleanups.push(f),
    say(text, cls) { chk(); const d = document.createElement("div"); d.className = "gm-line" + (cls ? " " + cls : ""); d.textContent = text; log.appendChild(d); },
    show(h) { chk(); act.innerHTML = h; return act; },
    hide() { chk(); act.innerHTML = ""; },
    sleep(ms) { return new Promise((res, rej) => { const t = setTimeout(() => { tok === token ? res() : rej(new Cancel()); }, ms); cleanups.push(() => clearTimeout(t)); }); },
    ask(prompt) {
      return new Promise(res => {
        chk();
        const id = "gm-in" + Math.random().toString(36).slice(2), f = document.createElement("form");
        f.className = "gm-ask";
        f.innerHTML = `<label for="${id}">${esc(prompt)}</label><div class="row"><input id="${id}" autocomplete="off" autocapitalize="off" spellcheck="false"><button class="btn primary">OK</button></div>`;
        f.onsubmit = e => { e.preventDefault(); const v = f.querySelector("input").value.trim(); f.remove(); ui.say(prompt + "  →  " + v, "gm-echo"); res(v); };
        act.appendChild(f); f.querySelector("input").focus({ preventScroll: true });
      });
    },
    wait(msg, label) {
      return new Promise(res => {
        chk();
        const d = document.createElement("div"); d.className = "gm-wait";
        d.innerHTML = (msg ? `<p>${esc(msg)}</p>` : "") + `<button type="button" class="btn primary">${esc(label || "Continue")}</button>`;
        d.querySelector("button").onclick = () => { d.remove(); res(); };
        act.appendChild(d); d.querySelector("button").focus({ preventScroll: true });
      });
    },
    choose(prompt, options, opts) {
      opts = opts || {};
      return new Promise(res => {
        chk();
        const d = document.createElement("div"); d.className = "gm-choose";
        d.innerHTML = `<p class="${opts.big ? "gm-big" : ""}">${esc(prompt)} <span class="gm-left"></span></p><div class="gm-opts"></div>`;
        const box = d.querySelector(".gm-opts"); let iv = null, to = null;
        const done = v => { clearInterval(iv); clearTimeout(to); d.remove(); if (!opts.private && !opts.quiet) ui.say(prompt + "  →  " + (options.find(o => o.value === v) || { label: v }).label, "gm-echo"); res(v); };
        options.forEach(o => { const b = document.createElement("button"); b.type = "button"; b.className = "btn" + (opts.primary ? " primary" : ""); b.textContent = o.label; b.onclick = () => done(o.value); box.appendChild(b); });
        if (opts.until) {
          const tick = () => { d.querySelector(".gm-left").textContent = Math.max(0, Math.ceil((opts.until - Date.now()) / 1000)) + "s left"; };
          tick(); iv = setInterval(tick, 250); to = setTimeout(() => done("timeout"), Math.max(0, opts.until - Date.now()));
          cleanups.push(() => { clearInterval(iv); clearTimeout(to); });
        }
        act.appendChild(d); const first = box.querySelector("button"); if (first) first.focus({ preventScroll: true });
      });
    },
    react() {
      return new Promise(res => {
        chk();
        const b = document.createElement("button"); b.type = "button"; b.className = "gm-react wait"; b.textContent = "Wait for GO...";
        act.appendChild(b); let go = false, t0 = 0, fired = false;
        const tm = setTimeout(() => { go = true; t0 = performance.now(); b.className = "gm-react go"; b.textContent = "GO! Tap now!"; }, 1500 + Math.random() * 2500);
        cleanups.push(() => clearTimeout(tm));
        const fire = () => { if (fired) return; fired = true; clearTimeout(tm); b.remove(); res(go ? performance.now() - t0 : Infinity); };
        b.addEventListener("pointerdown", fire);
        b.addEventListener("keydown", e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fire(); } });
        b.focus({ preventScroll: true });
      });
    },
  };
  return ui;
}

/* ====================== the games ====================== */
async function trivia(ui) {
  for (const [q, a] of sample(TRIVIA, 4)) for (const p of players) {
    const ans = (await ui.ask(`[${p}] ${q}`)).toLowerCase();
    if (ans === a) { ui.say("Correct! +1", "ok"); add(p, 1); } else ui.say("Nope! Answer: " + a, "bad");
  }
}
async function scrambleGame(ui) {
  for (const w of sample(WORDS, 4)) { const s = scramble(w);
    for (const p of players) {
      const ans = (await ui.ask(`[${p}] Unscramble '${s}'`)).toLowerCase();
      if (ans === w) { ui.say("Correct! +1", "ok"); add(p, 1); } else ui.say(`Nope! It was '${w}'`, "bad");
    }
  }
}
async function riddles(ui) {
  for (const [q, a] of RIDDLES) {
    ui.say(q, "gm-big");
    for (const p of players) { if ((await ui.ask(`[${p}] Your answer`)).toLowerCase() === a) { ui.say("Correct! +1", "ok"); add(p, 1); } }
    ui.say("(Answer: " + a + ")");
  }
}
async function reaction(ui) {
  ui.say("When the button says GO!, tap it as fast as you can. Tapping early is a false start.");
  const times = {};
  for (const p of players) {
    await ui.wait(`[${p}] Ready? Press Start, then wait for GO.`, "Start");
    times[p] = await ui.react();
    ui.say(times[p] === Infinity ? `${p}: false start!` : `${p}: ${(times[p] / 1000).toFixed(3)}s`, times[p] === Infinity ? "bad" : "");
  }
  const ok = players.filter(p => isFinite(times[p]));
  if (!ok.length) return ui.say("Everyone false-started. No winner.");
  const w = ok.reduce((a, b) => (times[a] <= times[b] ? a : b));
  ui.say(`Fastest: ${w}! +2`, "ok"); add(w, 2);
}
async function rps(ui) {
  const [a, b] = players, beats = { rock: "scissors", paper: "rock", scissors: "paper" };
  const opts = [{ label: "✊ Rock", value: "rock" }, { label: "✋ Paper", value: "paper" }, { label: "✌ Scissors", value: "scissors" }];
  for (let r = 1; r <= 3; r++) {
    ui.say(`Round ${r} of 3`, "gm-big");
    const ma = await ui.choose(`[${a}] choose (private, then hand over the screen)`, opts, { private: true });
    const mb = await ui.choose(`[${b}] choose (private)`, opts, { private: true });
    if (ma === mb) ui.say(`Both chose ${ma}. Tie!`);
    else if (beats[ma] === mb) { ui.say(`${a} wins (${ma} beats ${mb})`, "ok"); add(a, 1); }
    else { ui.say(`${b} wins (${mb} beats ${ma})`, "ok"); add(b, 1); }
  }
}
async function heads(ui) {
  for (const p of players) {
    await ui.wait(`[${p}] Hold the screen to your forehead (or let it float!). ${CFG.headsSeconds} seconds on the clock.`, "Start");
    const end = Date.now() + CFG.headsSeconds * 1000; let got = 0;
    for (const word of shuffle(HEADS)) {
      if (Date.now() >= end) break;
      const r = await ui.choose(word.toUpperCase(), [{ label: "Got it", value: "got" }, { label: "Skip", value: "skip" }], { until: end, big: true, quiet: true, primary: true });
      if (r === "got") got++; else if (r === "timeout") break;
    }
    ui.say(`Time! ${p} got ${got}`, "ok"); add(p, got);
  }
}
async function imposter(ui) {
  const word = pick(SPY_WORDS), spy = pick(players);
  for (const p of players) {
    await ui.wait(`[${p}] Only you should see the screen.`, "Reveal");
    ui.show(`<div class="gm-secret">${p === spy ? "You are the IMPOSTER! Blend in." : "Secret word: " + esc(word)}</div>`);
    await ui.wait("", "Hide"); ui.hide();
  }
  ui.say("Each person says one hint word, then vote on the imposter!", "gm-big");
  await ui.wait("", "Reveal the imposter");
  ui.say(`The imposter was ${spy}. The word was ${word}.`);
  const caught = await ui.choose("Did the crew catch them?", [{ label: "Yes", value: "y" }, { label: "No", value: "n" }]);
  if (caught === "y") players.forEach(p => { if (p !== spy) add(p, 1); }); else add(spy, 3);
}
async function ludo(ui) {
  const L = players.slice(0, 4), pos = L.map(() => [-1, -1, -1, -1]); let t = 0;
  const desc = p => (p < 0 ? "base" : p === 57 ? "home" : "step " + p);
  ui.say(`Ludo for ${L.join(", ")}. Roll a 6 to leave base. Landing on another token sends it home. Get all four home to win.`);
  for (;;) {
    const a = await ui.choose(`${L[t]}'s turn`, [{ label: "🎲 Roll", value: "r" }, { label: "Quit", value: "q" }], { quiet: true, primary: true });
    if (a === "q") return ui.say("Ludo stopped.");
    const roll = 1 + Math.floor(Math.random() * 6), moves = ludoMoves(pos[t], roll);
    ui.say(`${L[t]} rolled ${roll}`);
    if (moves.length) {
      ui.say("Tokens: " + pos[t].map((p, k) => `${k + 1}: ${desc(p)}`).join(", "));
      const k = moves.length === 1 ? moves[0] : await ui.choose("Move which token?", moves.map(m => ({ label: `Token ${m + 1} (${desc(pos[t][m])})`, value: m })), { quiet: true });
      for (const o of ludoMove(pos, t, k, roll)) ui.say(`Captured one of ${L[o]}'s tokens!`, "bad");
      if (pos[t].every(p => p === 57)) { ui.say(`${L[t]} wins Ludo!`, "ok"); add(L[t], 5); return; }
    } else ui.say("No moves.");
    if (roll !== 6) t = (t + 1) % L.length;
  }
}
async function chess(ui) {
  const b = newBoard(); let white = true;
  ui.say("No check rules: capture the king to win. Tap a piece, then tap where it should go. Pawns promote to queens.");
  ui.show(`<p id="gm-turn" class="gm-big"></p><div class="gm-chess" id="gm-chess"></div><div class="row"><button type="button" class="btn" id="gm-cq">Quit chess</button></div>`);
  const grid = $("gm-chess"), sq = [], name = i => "abcdefgh"[i % 8] + (8 - Math.floor(i / 8));
  for (let i = 0; i < 64; i++) { const s = document.createElement("button"); s.type = "button"; s.dataset.i = i; s.className = "gm-sq " + ((Math.floor(i / 8) + i % 8) % 2 ? "dk" : "lt"); grid.appendChild(s); sq.push(s); }
  const paint = (sel, tg) => sq.forEach((s, i) => {
    const p = b[i];
    s.textContent = p === " " ? "" : GLYPH[p.toLowerCase()];
    s.classList.toggle("pw", isWhite(p)); s.classList.toggle("pb", p !== " " && !isWhite(p));
    s.classList.toggle("sel", i === sel); s.classList.toggle("tgt", tg.includes(i));
    s.setAttribute("aria-label", name(i) + (p === " " ? " empty" : (isWhite(p) ? " white " : " black ") + PIECE[p.toLowerCase()]) + (tg.includes(i) ? ", can move here" : ""));
  });
  for (;;) {
    const who = white ? players[0] : players[1];
    $("gm-turn").textContent = `${who} (${white ? "white" : "black"}) to move`;
    const mv = await new Promise(res => {
      let sel = null, tg = []; paint(null, []);
      grid.onclick = e => {
        const s = e.target.closest(".gm-sq"); if (!s) return;
        const i = +s.dataset.i, p = b[i];
        if (sel !== null && tg.includes(i)) return res([sel, i]);
        if (p !== " " && isWhite(p) === white) { sel = i; tg = chessMoves(b, i); paint(sel, tg); } else { sel = null; tg = []; paint(null, []); }
      };
      $("gm-cq").onclick = () => res(null);
    });
    if (!mv) { grid.onclick = null; return ui.say("Chess stopped."); }
    const [f, t] = mv, cap = b[t]; b[t] = b[f]; b[f] = " ";
    if (b[t] === "P" && t < 8) b[t] = "Q";
    if (b[t] === "p" && t > 55) b[t] = "q";
    if (cap !== " " && cap.toLowerCase() === "k") { paint(null, []); grid.onclick = null; $("gm-turn").textContent = who + " wins!"; ui.say(`${who} wins! +5`, "ok"); add(who, 5); return; }
    white = !white;
  }
}
async function memory(ui) {
  const cards = sample(MEMORY_ICONS.concat(MEMORY_ICONS), 16), shown = Array(16).fill(false);
  window.__zg.lastMemory = cards;
  ui.show(`<div class="gm-mem" id="gm-mem"></div><p id="gm-mm" class="small">Moves: 0</p>`);
  const grid = $("gm-mem"), btn = [];
  for (let i = 0; i < 16; i++) { const b = document.createElement("button"); b.type = "button"; b.className = "gm-card"; b.dataset.i = i; b.setAttribute("aria-label", "Card " + (i + 1) + ", face down"); b.textContent = ""; grid.appendChild(b); btn.push(b); }
  const face = (i, up) => { btn[i].textContent = up ? cards[i] : ""; btn[i].classList.toggle("up", up); btn[i].setAttribute("aria-label", "Card " + (i + 1) + (up ? ", " + cards[i] : ", face down")); };
  let first = null, lock = false, moves = 0;
  await new Promise(res => {
    grid.onclick = e => {
      const b = e.target.closest(".gm-card"); if (!b || lock) return;
      const i = +b.dataset.i; if (shown[i] || i === first) return;
      face(i, true);
      if (first === null) { first = i; return; }
      moves++; $("gm-mm").textContent = "Moves: " + moves;
      if (cards[first] === cards[i]) { shown[first] = shown[i] = true; btn[first].classList.add("done"); btn[i].classList.add("done"); first = null; if (shown.every(Boolean)) res(); }
      else { lock = true; const a = first; first = null; const t = setTimeout(() => { face(a, false); face(i, false); lock = false; }, 900); cleanups.push(() => clearTimeout(t)); }
    };
  });
  grid.onclick = null; ui.say(`Done in ${moves} moves!`, "ok");
}
async function orbit(ui) {
  ui.say("Fly your astronaut around the planet. Dodge the grey rocks, grab the yellow stars (+5). Arrow keys or the buttons change lane.");
  ui.show(`<canvas id="gm-cv" width="400" height="400" aria-label="Orbit Runner game"></canvas><div class="row"><button type="button" class="btn" id="gm-in">◀ Inner lane</button><button type="button" class="btn" id="gm-out">Outer lane ▶</button></div>`);
  const cv = $("gm-cv"), ctx = cv.getContext("2d"), R = [70, 115, 160], s = { lane: 1, a: 0, sp: 0.025, bonus: 0, nx: 1.5, obs: [] };
  const inner = () => { s.lane = Math.max(0, s.lane - 1); }, outer = () => { s.lane = Math.min(2, s.lane + 1); };
  const key = e => { if (["ArrowLeft", "ArrowDown"].includes(e.key)) { inner(); e.preventDefault(); } if (["ArrowRight", "ArrowUp"].includes(e.key)) { outer(); e.preventDefault(); } };
  document.addEventListener("keydown", key); ui.cleanup(() => document.removeEventListener("keydown", key));
  $("gm-in").onclick = inner; $("gm-out").onclick = outer;
  const pt = (g, l) => { const f = g - s.a + Math.PI / 2; return [200 + R[l] * Math.cos(f), 200 + R[l] * Math.sin(f)]; };
  const dot = (x, y, r, c) => { ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fillStyle = c; ctx.fill(); };
  const score = () => Math.floor(s.a * 8) + s.bonus;
  await new Promise(res => {
    const iv = setInterval(() => {
      s.a += s.sp; s.sp = Math.min(s.sp + 0.00002, 0.065);
      if (s.a + 2.8 > s.nx) { s.obs.push([s.nx, Math.floor(Math.random() * 3), Math.random() < 0.3]); s.nx += 0.4 + Math.random() * 0.5; }
      const keep = []; let dead = false;
      for (const [g, l, star] of s.obs) {
        if (l === s.lane && Math.abs(g - s.a) < 0.13) { if (star) { s.bonus += 5; continue; } dead = true; }
        if (g > s.a - 0.6) keep.push([g, l, star]);
      }
      s.obs = keep;
      ctx.fillStyle = "#0a0f24"; ctx.fillRect(0, 0, 400, 400);
      ctx.lineWidth = 1; ctx.strokeStyle = "#2c3d7a"; R.forEach(r => { ctx.beginPath(); ctx.arc(200, 200, r, 0, 7); ctx.stroke(); });
      dot(200, 200, 35, "#2a6fdb");
      keep.forEach(([g, l, star]) => { const [x, y] = pt(g, l); dot(x, y, 9, star ? "#facc15" : "#9ca3af"); });
      const [x, y] = pt(s.a, s.lane); dot(x, y, 11, "#fff");
      ctx.font = "14px Arial"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillStyle = "#000"; ctx.fillText("🧑‍🚀", x, y);
      ctx.textAlign = "left"; ctx.textBaseline = "top"; ctx.fillStyle = "#fff"; ctx.fillText("Score " + score(), 10, 10);
      window.__zg.orbitScore = score();
      if (dead) { clearInterval(iv); ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.font = "22px Arial"; ctx.fillStyle = "#fff"; ctx.fillText("Crashed! Score " + score(), 200, 200); ui.say("Crashed! Score " + score(), "bad"); res(); }
    }, 16);
    ui.cleanup(() => clearInterval(iv));
  });
}

const GAMES = [
  { name: "Space Trivia", note: "Take turns", min: 1, fn: trivia },
  { name: "Word Scramble", note: "Take turns", min: 1, fn: scrambleGame },
  { name: "Riddles", note: "Everyone answers", min: 1, fn: riddles },
  { name: "Reaction Race", note: "Fastest wins +2", min: 1, fn: reaction },
  { name: "Rock Paper Scissors", note: "2 players", min: 2, fn: rps },
  { name: "Heads Up (space edition)", note: "Timed, take turns", min: 1, fn: heads },
  { name: "Imposter", note: "3 or more players", min: 3, fn: imposter },
  { name: "Ludo", note: "2 to 4 players", min: 2, fn: ludo },
  { name: "Chess", note: "2 players", min: 2, fn: chess },
  { name: "Orbit Runner", note: "Solo", min: 1, fn: orbit },
  { name: "Memory Match", note: "Solo", min: 1, fn: memory },
];

/* ====================== page wiring ====================== */
async function run(g) {
  stopAll(); token++;
  const tok = token, ui = makeUI(tok), card = $("gm-stagecard");
  card.hidden = false; $("gm-title").textContent = g.name; $("gm-log").innerHTML = ""; $("gm-act").innerHTML = "";
  if (players.length < g.min) { ui.say(`${g.name} needs at least ${g.min} players. Add names in Crew above.`, "bad"); return; }
  card.scrollIntoView({ block: "start" });
  try {
    await g.fn(ui);
    const d = document.createElement("div"); d.className = "row";
    d.innerHTML = `<button type="button" class="btn primary">Play again</button>`; d.querySelector("button").onclick = () => run(g);
    $("gm-act").appendChild(d);
  } catch (e) { if (!(e instanceof Cancel)) { console.error(e); ui.say("Something went wrong in this game. Try again.", "bad"); } }
}

function setCrew(text) {
  const seen = new Set();
  players = text.split(",").map(n => n.trim().slice(0, 20)).filter(n => n && !seen.has(n.toLowerCase()) && seen.add(n.toLowerCase())).slice(0, 8);
  if (!players.length) players = ["Crew"];
  const keep = {}; players.forEach(p => { keep[p] = scores[p] || 0; }); scores = keep;
  try { localStorage.setItem(KEY, players.join(", ")); } catch (e) {}
  $("gm-crew").value = players.join(", "); $("gm-crewmsg").textContent = players.length + " player" + (players.length === 1 ? "" : "s") + " ready."; renderScores();
}

window.__zg = { TRIVIA, WORDS, RIDDLES, CFG, lastMemory: null, orbitScore: 0 };   // test hooks
$("gm-menu").innerHTML = GAMES.map((g, i) => `<button type="button" class="gm-game" data-i="${i}"><b>${esc(g.name)}</b><span>${esc(g.note)}</span></button>`).join("");
$("gm-menu").onclick = e => { const b = e.target.closest(".gm-game"); if (b) run(GAMES[+b.dataset.i]); };
$("gm-save").onclick = () => setCrew($("gm-crew").value);
$("gm-crew").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); setCrew($("gm-crew").value); } };
$("gm-reset").onclick = () => { scores = {}; renderScores(); };
$("gm-stop").onclick = () => { stopAll(); token++; $("gm-stagecard").hidden = true; };
let saved = ""; try { saved = localStorage.getItem(KEY) || ""; } catch (e) {}
setCrew(saved);
})();
