/* ZENAUT sleep reminders: wind-down, avoid bright light, avoid blue light, lights out.
   Runs on every page. Off until turned on in Sleep > Reminders. Works only while ZENAUT is open. */
(function () {
  var K_ON = "zenaut_sleep_notify", K_TINT = "zenaut_sleep_tint", K_SND = "zenaut_sleep_sound", K_FIRED = "zenaut_sleep_fired";
  var URL = window.ZENAUT_SCHEDULE_URL || "/sleepcare/api/schedule";
  var sched = null, overlay = null, toast = null;

  function get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
  function put(k, v) { try { localStorage.setItem(k, v); } catch (e) {} }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function dayKey(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }

  function css() {
    var s = document.createElement("style");
    s.textContent =
      "#sn-toast{position:fixed;right:16px;bottom:16px;max-width:340px;z-index:10000;background:#152550;color:#EAF0FF;border:1px solid #FFB347;" +
      "border-left:6px solid #FFB347;border-radius:14px;padding:.9rem 1rem;box-shadow:0 8px 30px rgba(0,0,0,.45);font:15px/1.45 system-ui,sans-serif}" +
      "#sn-toast b{display:block;font-size:1rem;margin-bottom:.25rem}#sn-toast a,#sn-toast button{color:#EAF0FF;margin-right:.6rem}" +
      "#sn-toast button{background:transparent;border:1px solid #AEB2B1;border-radius:999px;padding:.25rem .8rem;cursor:pointer;font:inherit}" +
      "#sn-tint{position:fixed;inset:0;pointer-events:none;z-index:9999;background:rgb(255,150,50);mix-blend-mode:multiply;transition:opacity 1s}";
    document.head.appendChild(s);
  }

  function chime() {
    if (get(K_SND) !== "on") return;
    try {
      var A = window.AudioContext || window.webkitAudioContext, c = new A(), t = c.currentTime;
      [523.25, 659.25].forEach(function (f, i) {
        var o = c.createOscillator(), g = c.createGain();
        o.type = "sine"; o.frequency.value = f; o.connect(g); g.connect(c.destination);
        g.gain.setValueAtTime(0.0001, t + i * 0.35); g.gain.exponentialRampToValueAtTime(0.12, t + i * 0.35 + 0.05);
        g.gain.exponentialRampToValueAtTime(0.0001, t + i * 0.35 + 0.9); o.start(t + i * 0.35); o.stop(t + i * 0.35 + 1);
      });
    } catch (e) {}
  }

  function show(title, msg, href) {
    if (toast) toast.remove();
    toast = document.createElement("div"); toast.id = "sn-toast"; toast.setAttribute("role", "status");
    var b = document.createElement("b"); b.textContent = title;
    var p = document.createElement("div"); p.textContent = msg; p.style.marginBottom = ".6rem";
    var a = document.createElement("a"); a.textContent = "Open"; a.href = href || "#";
    var x = document.createElement("button"); x.type = "button"; x.textContent = "Dismiss"; x.onclick = function () { toast.remove(); toast = null; };
    toast.appendChild(b); toast.appendChild(p); toast.appendChild(a); toast.appendChild(x);
    document.body.appendChild(toast);
    chime();
    if (window.Notification && Notification.permission === "granted") {
      try { new Notification("ZENAUT · " + title, { body: msg }); } catch (e) {}
    }
  }

  function load() {
    fetch(URL, { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (j) { sched = j; tint(); }).catch(function () {});
  }

  function check() {
    if (!sched || get(K_ON) !== "on") return;
    var n = new Date(), m = n.getHours() * 60 + n.getMinutes(), fired;
    try { fired = JSON.parse(get(K_FIRED) || "[]"); } catch (e) { fired = []; }
    sched.events.forEach(function (e) {
      if (!e.enabled) return;
      var diff = (m - e.at + 1440) % 1440;
      if (diff > 10) return;                               /* only within 10 minutes of the time */
      var occ = new Date(n.getTime() - diff * 60000), id = dayKey(occ) + ":" + e.key;
      if (fired.indexOf(id) >= 0) return;
      fired.push(id); put(K_FIRED, JSON.stringify(fired.slice(-40)));
      show(e.title, e.msg, e.href);
    });
  }

  /* Evening warm tint: ramps from 8% to 35% over the dim period, 50% inside the sleep window */
  function tint() {
    if (get(K_TINT) !== "on" || !sched) { if (overlay) overlay.style.opacity = 0; return; }
    if (!overlay) { overlay = document.createElement("div"); overlay.id = "sn-tint"; overlay.style.opacity = 0; document.body.appendChild(overlay); }
    var n = new Date(), m = n.getHours() * 60 + n.getMinutes(), a = 0;
    var sleepDur = (sched.wake - sched.bed + 1440) % 1440, since = (m - sched.bed + 1440) % 1440, toBed = (sched.bed - m + 1440) % 1440;
    if (since < sleepDur) a = 0.5;
    else if (toBed <= sched.dim * 1) a = 0.08 + (sched.dim - toBed) / sched.dim * 0.27;
    overlay.style.opacity = a;
  }

  window.ZenautSleep = {
    refresh: load, tint: tint,
    test: function () { show("Test reminder", "Time to unwind: dim the lights, switch screens to night mode and start your wind-down.", "/sleepcare/wind-down"); }
  };

  css();
  load();
  setInterval(check, 20000);
  setInterval(tint, 60000);
  setInterval(load, 300000);
  setTimeout(check, 1500);
})();
