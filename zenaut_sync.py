"""ZENAUT · Your wellbeing sync: copies what you record in the other tabs into the daily table that feeds
the daily graphs, scores and weekly report. Nothing in the other tabs is changed or moved.

Sources and how they become one value per day:
  Physical health   heart rate, blood oxygen, systolic/diastolic, blood sugar: average of that day's readings
                    radiation: the day's highest dose (each entry is "dose today")
                    hydration: percentage -> litres, where 100 % = 2.5 L (the daily target used by the scores)
  Mood Monitor      mood 1-5 -> 1-10; stress 1-5 -> 0-100 (sample data from "Load sample data" is ignored)
  Stress check      total 0-32 -> 0-100, averaged with Mood Monitor stress when both exist
  Journal           mood 1-5 -> 1-10, averaged with Mood Monitor mood when both exist
  Sleep monitor     hours slept and sleep score
Values from these tabs replace the same day's value in the daily table; anything you typed into the quick
"Log today" form is kept for measurements that no tab provides. If you delete a reading in its own tab, the next
sync removes it from Your wellbeing too. Demo data disappears as soon as real data exists.
"""
from datetime import datetime
from statistics import mean

from zenaut_reports import FIELDS, db, save_day

HYDRATION_TARGET_L = 2.5

# measure shown to the person -> (where it comes from, fields in the daily table)
SOURCES = [
    ("Mood (1-10)", "Mood Monitor, Journal", ["mood"]),
    ("Stress (0-100)", "Mood Monitor, Stress check", ["stress"]),
    ("Sleep hours and score", "Sleep monitor", ["sleep_hours", "sleep_score"]),
    ("Heart rate", "Physical health", ["heart_rate"]),
    ("Blood oxygen", "Physical health", ["spo2"]),
    ("Blood pressure", "Physical health", ["systolic", "diastolic"]),
    ("Blood sugar", "Physical health", ["blood_sugar"]),
    ("Hydration (litres)", "Physical health (100 % = 2.5 L)", ["hydration_l"]),
    ("Radiation (mSv)", "Physical health", ["radiation_msv"]),
]


def _add(store, day, field, value):
    if value is not None:
        store.setdefault(day, {}).setdefault(field, []).append(float(value))


def _physical(store):
    from zenaut_physical import READINGS, load, series_of
    d = load()
    for slug in READINGS:
        for r in series_of(d, slug):
            day = r["time"][:10]
            if slug == "heart-rate":
                _add(store, day, "heart_rate", r["value"])
            elif slug == "blood-oxygen":
                _add(store, day, "spo2", r["value"])
            elif slug == "blood-pressure":
                _add(store, day, "systolic", r["sys"])
                _add(store, day, "diastolic", r["dia"])
            elif slug == "blood-sugar":
                _add(store, day, "blood_sugar", r["value"])
            elif slug == "hydration":
                _add(store, day, "hydration_l", r["value"] / 100.0 * HYDRATION_TARGET_L)
            elif slug == "radiation":
                _add(store, day, "radiation_msv", r["value"])   # reduced to the day's highest below


def _mood_monitor(store):
    from zenaut_mood import load_log
    for e in load_log():
        if e.get("sample"):
            continue
        _add(store, e["date"], "mood", 1 + (e["mood"] - 1) * 9 / 4)
        _add(store, e["date"], "stress", (e["stress"] - 1) * 25)


def _stress_check(store):
    from zenaut_stress import day_of, load_history
    for e in load_history():
        _add(store, day_of(e), "stress", e["total"] / 32 * 100)


def _journal(store):
    from zenaut_astrocare import load
    for e in load().get("journal", []):
        m = e.get("mood")
        if isinstance(m, (int, float)) and 1 <= m <= 5 and e.get("ts"):
            _add(store, str(e["ts"])[:10], "mood", 1 + (m - 1) * 9 / 4)


def _sleep(store):
    from zenaut_sleep import load
    for e in load().get("log", []):
        _add(store, e["date"], "sleep_hours", e["dur"] / 60.0)
        _add(store, e["date"], "sleep_score", e["score"])


def sync_all():
    """Recompute the daily table from the other tabs. Returns a summary dict. Never raises."""
    store, problems = {}, []
    for name, fn in (("Physical health", _physical), ("Mood Monitor", _mood_monitor), ("Stress check", _stress_check),
                     ("Journal", _journal), ("Sleep monitor", _sleep)):
        try:
            fn(store)
        except Exception as e:  # one broken source must not stop the others
            problems.append(f"{name}: {e}")
    # one number per day and field
    values = {}
    for day, fields in store.items():
        values[day] = {f: (max(v) if f == "radiation_msv" else round(mean(v), 2)) for f, v in fields.items()}
    counts = {f: sum(1 for v in values.values() if f in v) for f in FIELDS}
    try:
        with db() as con:
            con.execute("CREATE TABLE IF NOT EXISTS synced(day TEXT, field TEXT, PRIMARY KEY(day, field))")
            old = {(r[0], r[1]) for r in con.execute("SELECT day, field FROM synced")}
            new = {(day, f) for day, fv in values.items() for f in fv}
            for day, field in old - new:                    # reading was deleted in its own tab: take it out here too
                con.execute(f"UPDATE daily SET {field}=NULL WHERE day=? AND demo=0", (day,))
            con.execute("DELETE FROM daily WHERE demo=0 AND " + " AND ".join(f"{f} IS NULL" for f in FIELDS))
            if values:
                con.execute("DELETE FROM daily WHERE demo=1")  # real data exists, so the demo is no longer needed
            con.execute("DELETE FROM synced")
            con.executemany("INSERT INTO synced VALUES(?,?)", sorted(new))
            existing = {r["day"]: r for r in con.execute("SELECT * FROM daily WHERE demo=0")}
        for day, fv in values.items():
            row = existing.get(day)
            data = {f: row[f] for f in FIELDS} if row is not None else {}
            data.update(fv)
            save_day(data, day=day, demo=0)
    except Exception as e:
        problems.append(f"Saving: {e}")
    return dict(time=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), days=len(values), counts=counts, problems=problems)
