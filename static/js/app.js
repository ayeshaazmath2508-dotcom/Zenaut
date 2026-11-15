var P = {};
try { P = JSON.parse(localStorage.getItem("zenaut_profile") || "{}"); } catch (e) {}
function save() { try { localStorage.setItem("zenaut_profile", JSON.stringify(P)); } catch (e) {} }
function $(id) { return document.getElementById(id); }

// Profile card, avatars
function initials(n) { return (n || "?").split(/\s+/).map(function (w) { return w[0]; }).join("").slice(0, 2).toUpperCase(); }
function paintAvatar(el) {
  if (!el) return;
  if (P.photo) { el.style.backgroundImage = "url(" + P.photo + ")"; el.textContent = ""; }
  else { el.style.backgroundImage = "none"; el.textContent = initials(P.name); }
}
["av", "av2", "av3"].forEach(function (i) { paintAvatar($(i)); });
if (P.name) $("me-name").textContent = P.name;
if (P.role || P.mission) $("me-role").textContent = [P.role, P.mission].filter(Boolean).join(" · ");

// Clock: mission elapsed time if a start is set, otherwise local time
function pad(n) { return (n < 10 ? "0" : "") + n; }
function hms(ms) { var s = Math.floor(ms / 1000); return pad(Math.floor(s / 3600) % 24) + ":" + pad(Math.floor(s / 60) % 60) + ":" + pad(s % 60); }
var clock = $("clock"), note = $("clock-note");
function tick() {
  if (!clock) return;
  var start = P.start ? new Date(P.start) : null, now = new Date();
  if (start && !isNaN(start)) {
    var d = now - start;
    if (d >= 0) { clock.textContent = hms(d); note.textContent = "Mission day " + (Math.floor(d / 864e5) + 1) + (P.mission ? " of " + P.mission : "") + ". Time since mission start."; }
    else { clock.textContent = "T-" + hms(-d); note.textContent = "Countdown to mission start (" + Math.floor(-d / 864e5) + " full days left)."; }
  } else {
    clock.textContent = pad(now.getHours()) + ":" + pad(now.getMinutes()) + ":" + pad(now.getSeconds());
    note.textContent = "Local time. Set a mission start in Profile to see mission time.";
  }
}
tick(); setInterval(tick, 1000);

// Mobile menu
$("menu").onclick = function () { $("side").classList.toggle("open"); };

// Profile page
if ($("save")) {
  $("f-name").value = P.name || ""; $("f-role").value = P.role || ""; $("f-mission").value = P.mission || "";
  $("f-start").value = P.start || ""; $("f-theme").value = P.theme || "dark";
  $("f-theme").onchange = function () { document.documentElement.dataset.theme = this.value; };
  $("photo").onchange = function () {
    var f = this.files[0]; if (!f) return;
    var r = new FileReader();
    r.onload = function () {
      var img = new Image();
      img.onload = function () {
        var c = document.createElement("canvas"), s = 200, m = Math.min(img.width, img.height);
        c.width = c.height = s;
        c.getContext("2d").drawImage(img, (img.width - m) / 2, (img.height - m) / 2, m, m, 0, 0, s, s);
        P.photo = c.toDataURL("image/jpeg", .85); paintAvatar($("av3"));
      };
      img.src = r.result;
    };
    r.readAsDataURL(f);
  };
  $("save").onclick = function () {
    P.name = $("f-name").value.trim(); P.role = $("f-role").value.trim(); P.mission = $("f-mission").value.trim();
    P.start = $("f-start").value; P.theme = $("f-theme").value;
    save(); $("saved").textContent = "Saved."; location.reload();
  };
}
