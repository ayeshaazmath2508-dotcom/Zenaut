/* ZENAUT · Physical health > Sync devices: Bluetooth connections (Web Bluetooth).
   Works in Chrome or Edge (desktop or Android) on http://127.0.0.1:5000. Firefox and Safari do not support it.
   Uses the standard Bluetooth health profiles: Heart Rate, Pulse Oximeter, Blood Pressure, Glucose.
   Devices that use a private (non-standard) protocol cannot be read directly: use file import, the device
   folder or the API on the same page instead.
   The parsers at the top are plain functions so they can be tested without a device. */
(function (root) {
  "use strict";

  // ---- parsers (DataView in, plain numbers out) ----
  function sfloat(dv, off) {                     // IEEE-11073 16-bit SFLOAT
    var raw = dv.getUint16(off, true);
    if (raw === 0x07FF || raw === 0x0800 || raw === 0x0801 || raw === 0x07FE || raw === 0x0802) return NaN;
    var mant = raw & 0x0FFF, exp = raw >> 12;
    if (mant >= 0x0800) mant -= 0x1000;
    if (exp >= 0x08) exp -= 0x10;
    return mant * Math.pow(10, exp);
  }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  function isoLocal(d) {                         // local time, no timezone, e.g. 2026-01-15T07:30:00
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function devTime(dv, off, extraMinutes) {      // 7-byte Bluetooth date-time; null if it looks wrong
    if (dv.byteLength < off + 7) return null;
    var y = dv.getUint16(off, true), d = new Date(y, dv.getUint8(off + 2) - 1, dv.getUint8(off + 3), dv.getUint8(off + 4), dv.getUint8(off + 5), dv.getUint8(off + 6));
    if (!y || isNaN(d.getTime())) return null;
    if (extraMinutes) d = new Date(d.getTime() + extraMinutes * 60000);
    return d.getTime() > Date.now() + 5 * 60000 ? null : d;   // a clock that is ahead is not trusted
  }
  function parseHeartRate(dv) {
    var flags = dv.getUint8(0);
    return (flags & 1) ? dv.getUint16(1, true) : dv.getUint8(1);
  }
  function parseSpO2(dv) {                       // spot-check (0x2A5E) and continuous (0x2A5F) both start: flags, SpO2, pulse
    if (dv.byteLength < 5) return null;
    return { spo2: sfloat(dv, 1), pulse: sfloat(dv, 3) };
  }
  function parseBP(dv) {
    var flags = dv.getUint8(0), k = (flags & 1) ? 7.50062 : 1;   // kPa -> mmHg
    var o = { sys: sfloat(dv, 1) * k, dia: sfloat(dv, 3) * k, time: null };
    if (flags & 2) o.time = devTime(dv, 7);
    return o;
  }
  function parseGlucose(dv) {
    var flags = dv.getUint8(0), off = 10, offset = 0;
    var time = devTime(dv, 3, 0);
    if (flags & 1) { offset = dv.getInt16(off, true); off += 2; time = devTime(dv, 3, offset); }
    if (!(flags & 2)) return null;               // no concentration in this record
    var c = sfloat(dv, off);
    return { mgdl: (flags & 4) ? c * 18016 : c * 100000, time: time };   // mol/L or kg/L -> mg/dL
  }
  var api = { sfloat: sfloat, parseHeartRate: parseHeartRate, parseSpO2: parseSpO2, parseBP: parseBP, parseGlucose: parseGlucose, isoLocal: isoLocal };
  if (typeof module !== "undefined" && module.exports) { module.exports = api; }
  if (typeof document === "undefined") return;

  // ---- page ----
  var box = document.getElementById("bt-root");
  if (!box) return;
  var SYNC_URL = box.dataset.sync;

  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text) e.textContent = text; return e; }
  function round(x, n) { var p = Math.pow(10, n || 0); return Math.round(x * p) / p; }
  function good(x) { return typeof x === "number" && isFinite(x); }

  if (!navigator.bluetooth) {
    var c = el("div", "card");
    c.appendChild(el("h2", "", "Bluetooth is not available in this browser"));
    c.appendChild(el("p", "", "Use Chrome or Edge (desktop or Android) and open ZENAUT at http://127.0.0.1:5000. Firefox and Safari cannot connect to Bluetooth devices. You can still use options 2, 3 and 4 below."));
    box.appendChild(c);
    return;
  }

  function post(deviceName, readings, ui) {
    ui.msg("Saving...");
    return fetch(SYNC_URL, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ device: deviceName, readings: readings }) })
      .then(function (r) { return r.json(); })
      .then(function (j) {
        if (!j.ok) { ui.msg("Could not save: " + (j.error || "unknown error")); return; }
        var extra = j.rejected && j.rejected.length ? " Problem: " + j.rejected[0] : "";
        ui.msg("Saved. " + j.summary + "." + extra);
      })
      .catch(function () { ui.msg("Could not reach ZENAUT to save. The reading is still shown here; try Save again."); });
  }

  // def.start(service, ui, deviceName) wires up notifications for one kind of device.
  var DEFS = [
    { title: "Heart-rate monitor", note: "Chest strap, watch or band that shares heart rate over Bluetooth.", service: 0x180D, continuous: true,
      start: async function (svc, ui, name) {
        var ch = await svc.getCharacteristic(0x2A37);
        await ch.startNotifications();
        ch.addEventListener("characteristicvaluechanged", function (e) {
          var v = parseHeartRate(e.target.value);
          if (v >= 20 && v <= 250) ui.sample("heart-rate", v, "bpm");
        });
      } },
    { title: "Pulse oximeter", note: "Blood oxygen and pulse. Press the measure button on the device after connecting.", service: 0x1822, continuous: true,
      start: async function (svc, ui, name) {
        var ok = 0;
        async function listen(uuid, discrete) {
          try {
            var ch = await svc.getCharacteristic(uuid);
            await ch.startNotifications();
            ch.addEventListener("characteristicvaluechanged", function (e) {
              var m = parseSpO2(e.target.value);
              if (!m || !good(m.spo2)) return;
              if (discrete) {            // a deliberate spot-check: save it straight away
                var rs = [{ type: "blood-oxygen", value: round(m.spo2, 1) }];
                if (good(m.pulse) && m.pulse >= 20 && m.pulse <= 250) rs.push({ type: "heart-rate", value: Math.round(m.pulse) });
                ui.live(round(m.spo2, 1) + " %"); post(name, rs, ui);
              } else {
                ui.sample("blood-oxygen", round(m.spo2, 1), "%");
                if (good(m.pulse) && m.pulse >= 20 && m.pulse <= 250) ui.sample("heart-rate", Math.round(m.pulse), "bpm", true);
              }
            });
            ok++;
          } catch (err) { /* this characteristic is not offered by the device */ }
        }
        await listen(0x2A5E, true);
        await listen(0x2A5F, false);
        if (!ok) throw new Error("This oximeter does not use the standard Bluetooth profile. Use file import, the device folder or the API instead.");
      } },
    { title: "Blood-pressure cuff", note: "Take a measurement on the cuff after connecting. It is saved automatically.", service: 0x1810, continuous: false,
      start: async function (svc, ui, name) {
        var ch = await svc.getCharacteristic(0x2A35);
        await ch.startNotifications();
        ch.addEventListener("characteristicvaluechanged", function (e) {
          var m = parseBP(e.target.value);
          if (!m || !good(m.sys) || !good(m.dia)) return;
          var r = { type: "blood-pressure", sys: Math.round(m.sys), dia: Math.round(m.dia) };
          if (m.time) r.time = isoLocal(m.time);
          ui.live(r.sys + "/" + r.dia + " mmHg"); post(name, [r], ui);
        });
      } },
    { title: "Glucose meter", note: "Downloads the readings stored on the meter. New ones are saved as they arrive.", service: 0x1808, continuous: false,
      start: async function (svc, ui, name) {
        var meas = await svc.getCharacteristic(0x2A18), batch = [], timer = null;
        function flush() {
          timer = null;
          if (!batch.length) return;
          var rs = batch; batch = [];
          ui.live(rs[rs.length - 1].value + " mg/dL"); post(name, rs, ui);
        }
        await meas.startNotifications();
        meas.addEventListener("characteristicvaluechanged", function (e) {
          var m = parseGlucose(e.target.value);
          if (!m || !good(m.mgdl)) return;
          var r = { type: "blood-sugar", value: round(m.mgdl, 0) };
          if (m.time) r.time = isoLocal(m.time);
          batch.push(r);
          clearTimeout(timer); timer = setTimeout(flush, 2000);
        });
        try {                                        // ask the meter to send everything it has stored
          var racp = await svc.getCharacteristic(0x2A52);
          await racp.startNotifications();
          var cmd = new Uint8Array([0x01, 0x01]);
          if (racp.writeValueWithResponse) await racp.writeValueWithResponse(cmd); else await racp.writeValue(cmd);
          ui.msg("Connected. Downloading stored readings...");
        } catch (err) { ui.msg("Connected. New readings will be saved as they arrive."); }
      } }
  ];

  DEFS.forEach(function (def) {
    var card = el("div", "card"), live = el("div", "live", "--"), msg = el("div", "msg", def.note);
    var row = el("div", "row"), connect = el("button", "btn primary", "Connect");
    connect.type = "button";
    card.appendChild(el("h2", "", def.title)); card.appendChild(live); card.appendChild(msg); card.appendChild(row);
    row.appendChild(connect);
    var buffers = {}, units = {}, hidden = {}, name = def.title, auto = null;
    var ui = {
      msg: function (t) { msg.textContent = t; },
      live: function (t) { live.textContent = t; },
      sample: function (type, v, unit, quiet) {           // continuous readings are averaged when saved
        (buffers[type] = buffers[type] || []).push(v); units[type] = unit;
        if (!quiet) { live.textContent = v + " " + unit; }
        saveBtn && (saveBtn.disabled = false);
        msg.textContent = "Collecting readings (" + Object.keys(buffers).map(function (k) { return buffers[k].length + " " + k; }).join(", ") + "). Press Save to store the average.";
      }
    };
    var saveBtn = null, autoBox = null;
    if (def.continuous) {
      saveBtn = el("button", "btn", "Save average now"); saveBtn.type = "button"; saveBtn.disabled = true;
      var lab = el("label", "small"); autoBox = document.createElement("input"); autoBox.type = "checkbox"; autoBox.style.width = "auto";
      lab.appendChild(autoBox); lab.appendChild(document.createTextNode(" Save automatically every 5 minutes"));
      row.appendChild(saveBtn); card.appendChild(lab);
      var saveNow = function () {
        var rs = [];
        Object.keys(buffers).forEach(function (t) {
          var b = buffers[t]; if (!b.length) return;
          var mean = b.reduce(function (a, x) { return a + x; }, 0) / b.length;
          rs.push({ type: t, value: round(mean, t === "blood-oxygen" ? 1 : 0) }); buffers[t] = [];
        });
        if (rs.length) { saveBtn.disabled = true; post(name, rs, ui); }
      };
      saveBtn.onclick = saveNow;
      autoBox.onchange = function () { clearInterval(auto); if (autoBox.checked) auto = setInterval(saveNow, 300000); };
    }
    connect.onclick = async function () {
      try {
        connect.disabled = true; ui.msg("Choose your device in the Bluetooth window...");
        var dev = await navigator.bluetooth.requestDevice({ filters: [{ services: [def.service] }] });
        name = dev.name || def.title;
        dev.addEventListener("gattserverdisconnected", function () { ui.msg("Disconnected. Press Connect to reconnect."); connect.disabled = false; connect.textContent = "Reconnect"; });
        ui.msg("Connecting to " + name + "...");
        var server = await dev.gatt.connect();
        var svc = await server.getPrimaryService(def.service);
        await def.start(svc, ui, name);
        connect.textContent = "Connected"; ui.msg("Connected to " + name + ". Waiting for readings...");
      } catch (err) {
        connect.disabled = false;
        ui.msg(err && err.name === "NotFoundError" ? "No device was chosen." : "Could not connect: " + (err && err.message ? err.message : err));
      }
    };
    box.appendChild(card);
  });
  box.className = "bt";
})(typeof window !== "undefined" ? window : globalThis);
