# ZENAUT dashboard

1. In VS Code: File > Open Folder... and pick this folder.
2. Open the terminal (Ctrl+`) and create a virtual environment:
   - Windows: `python -m venv .venv` then `.venv\Scripts\activate`
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. Ctrl+Shift+P > "Python: Select Interpreter" > choose the `.venv` one.
5. `python app.py` then open http://127.0.0.1:5000

Note: White noise needs numpy. If you see 'No module named numpy', run `pip install -r requirements.txt` again.

Optional: for a reliable NASA picture in the Journal, get a free key at https://api.nasa.gov and set it before running:
- Windows: `set NASA_API_KEY=yourkey`
- macOS/Linux: `export NASA_API_KEY=yourkey`

## Sleep & circadian centre (new)
Open **Sleep** in the sidebar (`/sleepcare`). Tabs: Overview, Circadian calculator, Sleep monitor, Light & solar, Wind-down & relax, Reminders.
- Code is in `zenaut_sleep.py`; reminders run from `static/js/sleepnotify.js` on every page.
- Reminders are off until you press "Turn reminders on" in Sleep > Reminders. They work only while ZENAUT is open in a browser.
- Data is saved in `sleep_data.json` (ignored by git). Nightly sleep is also copied to "Your wellbeing".

## Music (new)
Open **Music** in the sidebar (`/music`). Upload your own songs (mp3, wav, ogg, m4a, aac, flac, opus), tag a mood, build playlists, favourites, shuffle, repeat and a sleep timer that fades out.
- Code: `zenaut_music.py`. Songs are stored in `zenaut_music_data/` on this device and play offline.
- Suggestions use your Mood Monitor check-ins, last night's sleep and your bedtime. Wind-down & relax has a link to Sleep music.

## Books (new)
Open **Books** in the sidebar (`/books`). Upload your own books (pdf, epub, txt, md, html, docx, rtf, mobi, azw3, cbz), search, download or remove them.
- Code: `zenaut_books.py`. Files are stored in `zenaut_books_data/` on this device, so they open with no internet.
- PDF and text books open and read in the browser; other formats are downloaded and opened in a reader app.

## Connection with Earth (changed)
Two tabs now:
- **Messages from home** (`/connection/`): loved ones send messages, notes, photos and videos. Everything that has arrived is saved in `zenaut_earth_data/` and can be read or watched offline. New items are downloaded when you are online.
- **Mission & emergency** (`/astro/earth`): everything that was on the old page (mission environment tips, message to loved ones, "I feel seriously ill" button, outbox).

How loved ones send things (code: `zenaut_earth.py`):
1. Run ZENAUT on a **ground computer** (for example `flask --app app run --host 0.0.0.0`, or `python app.py` with `host="0.0.0.0"`). Your family opens `http://<ground-computer>:5000/connection/portal`, types the **family code** shown on that computer's *Messages from home* page, and sends.
2. On the **astronaut's** computer open *Messages from home > Connection settings* and enter the ground computer's address and its family code. Whenever the astronaut is online ZENAUT downloads anything new (when the page is open, and every 5 minutes in the background).
The family code is a simple shared password. For real use put the ground computer behind HTTPS and a VPN or proper login.

## Physical health: Sync devices (new)
Open **Physical health > Sync devices** (`/physical/devices`). Typing readings by hand works exactly as before. Hardware readings go into the same tabs, with the same safe ranges, alerts and graphs, and are labelled "synced from <device>". Repeated syncs never create duplicates.
1. **Bluetooth** (Chrome or Edge, open `http://127.0.0.1:5000`): heart-rate monitors, pulse oximeters, blood-pressure cuffs and glucose meters that use the standard Bluetooth health profiles. Devices with a private protocol cannot be read this way.
2. **File import**: CSV or JSON exported by a device or its app (example CSV on the page).
3. **Device folder**: drop CSV/JSON files into `device_inbox/` and press *Sync from device folder*.
4. **API** for gateway programs: `POST /physical/api/sync`. Optional password: set `ZENAUT_DEVICE_TOKEN` and send it as the `X-Device-Token` header.
Code: `zenaut_devices.py`, `static/js/devices.js`.

## Your wellbeing: synced with every tab (changed)
Daily graphs, scores and the weekly report now fill themselves in from Physical health, Mood Monitor, Stress check, Sleep and Journal each time you open them (or press *Sync now*). Code: `zenaut_sync.py`.
- Each day uses the average of that day's readings (radiation uses the highest dose; hydration % is converted to litres with 100 % = 2.5 L).
- Mood 1-5 becomes 1-10 and stress 1-5 / 0-32 becomes 0-100. Sample data from the Mood Monitor is ignored.
- A score uses only the measurements that exist for that day; days with nothing show "no data" instead of invented numbers. "Week" means the last 7 calendar days.
- If you delete a reading in its own tab it disappears from Your wellbeing at the next sync. Demo data is removed as soon as real data exists.

## Removed
The Chatbot (Aria) page, its sidebar item and its saved chat history.

