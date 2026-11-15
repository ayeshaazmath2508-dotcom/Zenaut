"""ZENAUT · Books, your offline library (Flask blueprint at /books).

Upload your own books (pdf, epub, txt, md, html, docx, rtf, mobi, azw3, cbz). Files are copied into
./zenaut_books_data/ on this device, so every book you have uploaded opens with no internet.
  - PDF and plain-text books (txt, md) open and read right in the browser.
  - Other formats (epub, docx, mobi ...) are kept in the library and can be downloaded to open
    in a reader app on the same device.
Not medical advice. Keep copies of books you cannot replace.
"""
import hashlib
import json
import re
import uuid
from datetime import datetime
from html import escape
from pathlib import Path

from flask import Blueprint, abort, redirect, render_template, request, send_file, url_for
from markupsafe import Markup

from zenaut_reports import CSS as WB_CSS

books = Blueprint("books", __name__, url_prefix="/books")

BASE = Path(__file__).with_name("zenaut_books_data")
FILES = BASE / "files"
DB_FILE = BASE / "library.json"

ALLOWED = ["pdf", "epub", "txt", "md", "html", "htm", "docx", "rtf", "mobi", "azw3", "cbz"]
MIME = {"pdf": "application/pdf", "txt": "text/plain; charset=utf-8", "md": "text/plain; charset=utf-8",
        "epub": "application/epub+zip", "html": "text/html", "htm": "text/html", "rtf": "application/rtf",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "mobi": "application/x-mobipocket-ebook", "azw3": "application/octet-stream", "cbz": "application/vnd.comicbook+zip"}
READABLE = {"pdf", "txt", "md"}  # opened inside the browser; the rest are downloaded

CSS = """
.wb .bk{display:flex;gap:.8rem;align-items:center;padding:.7rem 0;border-bottom:1px solid rgba(174,178,177,.2);flex-wrap:wrap}
.wb .bk:last-child{border:0}
.wb .bk .cover{width:44px;height:58px;border-radius:6px;background:var(--blue);color:#fff;display:flex;align-items:center;
 justify-content:center;font-weight:800;font-size:.8rem;flex-shrink:0}
.wb .bk .info{flex:1;min-width:180px}.wb .bk .info b{display:block}
.wb .bk form{display:inline}
.wb .btn.sm{padding:.25rem .8rem;font-size:.75rem}
.wb .row{display:flex;gap:.6rem;align-items:center;flex-wrap:wrap;margin-top:.8rem}
.wb input[type=file]{padding:.6rem}
"""

# ------------------------------------------------------------------ storage
def load_db():
    FILES.mkdir(parents=True, exist_ok=True)
    try:
        db = json.loads(DB_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        db = {}
    db.setdefault("books", [])
    return db

def save_db(db):
    FILES.mkdir(parents=True, exist_ok=True)
    DB_FILE.write_text(json.dumps(db, indent=2), encoding="utf-8")

def clean_title(filename):
    stem = Path(filename).stem
    return re.sub(r"[_\-]+", " ", stem).strip().title() or "Untitled"

def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024

def find(db, bid):
    return next((b for b in db["books"] if b["id"] == bid), None)

def add_book(db, filename, data, title="", author=""):
    """Copy an uploaded book into the library. Returns (book, reason_if_skipped)."""
    ext = Path(filename).suffix.lower().lstrip(".")
    if ext not in ALLOWED:
        return None, f"{filename}: file type .{ext or '?'} is not supported"
    if not data:
        return None, f"{filename}: the file is empty"
    digest = hashlib.sha1(data).hexdigest()
    if any(b["hash"] == digest for b in db["books"]):
        return None, f"{filename}: already in your library"
    bid = uuid.uuid4().hex[:10]
    stored = f"{bid}.{ext}"
    FILES.mkdir(parents=True, exist_ok=True)
    (FILES / stored).write_bytes(data)
    book = dict(id=bid, title=(title or clean_title(filename))[:150], author=(author or "").strip()[:100], ext=ext,
                file=stored, size=len(data), hash=digest, added=datetime.now().strftime("%Y-%m-%d %H:%M"),
                original=Path(filename).name[:150])
    db["books"].append(book)
    return book, None

# ------------------------------------------------------------------ pages
def page(body):
    return render_template("wellbeing.html", title="Your offline library", links="", heading="Books",
                           body=Markup(f"<style>{CSS}</style>{body}"), css=Markup(WB_CSS), active="books")

@books.route("/")
def library():
    db = load_db()
    q = request.args.get("q", "").strip().lower()
    items = [b for b in db["books"] if q in (b["title"] + " " + b["author"]).lower()]
    items.sort(key=lambda b: b["title"].lower())
    note = ""
    if request.args.get("added"):
        note += f'<div class="alert green">Added {escape(request.args["added"])} book(s) to your library.</div>'
    if request.args.get("skipped"):
        note += f'<div class="alert orange">{escape(request.args["skipped"])}</div>'
    rows = ""
    for b in items:
        if b["ext"] in READABLE:
            open_btn = f'<a class="btn primary sm" href="{url_for("books.read", bid=b["id"])}" target="_blank" rel="noopener">Read</a>'
        else:
            open_btn = ""
        rows += (f'<div class="bk"><div class="cover">{escape(b["ext"].upper())}</div>'
                 f'<div class="info"><b>{escape(b["title"])}</b>'
                 f'<span class="small">{escape(b["author"] or "Unknown author")} · {human(b["size"])} · added {escape(b["added"])}</span></div>'
                 f'{open_btn} <a class="btn sm" href="{url_for("books.download", bid=b["id"])}">Download</a>'
                 f'<form method="post" action="{url_for("books.delete", bid=b["id"])}" onsubmit="return confirm(\'Remove this book from your library?\')">'
                 '<button class="btn sm">Remove</button></form></div>')
    if not rows:
        rows = ('<p class="small">No books match your search.</p>' if q else
                '<p class="small">Your library is empty. Upload your first book above.</p>')
    accept = ",".join("." + e for e in ALLOWED)
    body = (note +
            '<div class="card"><h2>Upload books</h2>'
            f'<form method="post" action="{url_for("books.upload")}" enctype="multipart/form-data">'
            f'<label for="bkfiles">Choose one or more books ({", ".join(ALLOWED)})</label>'
            f'<input type="file" id="bkfiles" name="files" accept="{accept}" multiple required>'
            '<div class="grid" style="margin-top:.6rem"><div><label for="bktitle">Title (optional, for a single book)</label>'
            '<input id="bktitle" name="title" maxlength="150" placeholder="Leave blank to use the file name"></div>'
            '<div><label for="bkauthor">Author (optional)</label><input id="bkauthor" name="author" maxlength="100"></div></div>'
            '<div class="row"><button class="btn primary">Upload to library</button></div></form>'
            '<p class="small" style="margin-top:.6rem">Books are saved on this device in the <code>zenaut_books_data</code> folder and work offline. '
            'PDF and text books open here; other formats can be downloaded and opened in a reader app.</p></div>'
            '<div class="card"><h2>Your library</h2>'
            f'<form method="get" class="row" style="margin-top:0"><input name="q" value="{escape(q)}" placeholder="Search by title or author" '
            'aria-label="Search books" style="max-width:320px"><button class="btn">Search</button></form>'
            f'<div style="margin-top:.6rem">{rows}</div>'
            f'<p class="small" style="margin-top:.6rem">{len(db["books"])} book(s) in total.</p></div>')
    return page(body)

@books.route("/upload", methods=["POST"])
def upload():
    db = load_db()
    files = [f for f in request.files.getlist("files") if f and f.filename]
    title = request.form.get("title", "").strip() if len(files) == 1 else ""
    author = request.form.get("author", "").strip()
    added, skipped = 0, []
    for f in files:
        book, why = add_book(db, f.filename, f.read(), title, author)
        if book:
            added += 1
        else:
            skipped.append(why)
    save_db(db)
    return redirect(url_for("books.library", added=added or None, skipped="; ".join(skipped) or None))

def book_or_404(bid):
    b = find(load_db(), bid)
    path = FILES / b["file"] if b else None
    if not b or not path.exists():
        abort(404)
    return b, path

@books.route("/<bid>/read")
def read(bid):
    b, path = book_or_404(bid)
    if b["ext"] not in READABLE:
        return redirect(url_for("books.download", bid=bid))
    return send_file(path, mimetype=MIME[b["ext"]], as_attachment=False)

@books.route("/<bid>/download")
def download(bid):
    b, path = book_or_404(bid)
    name = re.sub(r'[\\/:*?"<>|]+', "_", b["title"]) + "." + b["ext"]
    return send_file(path, mimetype=MIME.get(b["ext"], "application/octet-stream"), as_attachment=True, download_name=name)

@books.route("/<bid>/delete", methods=["POST"])
def delete(bid):
    db = load_db()
    b = find(db, bid)
    if b:
        try:
            (FILES / b["file"]).unlink()
        except FileNotFoundError:
            pass
        db["books"] = [x for x in db["books"] if x["id"] != bid]
        save_db(db)
    return redirect(url_for("books.library"))
