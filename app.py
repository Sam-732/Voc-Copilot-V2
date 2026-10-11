"""
VoC Copilot local web app (v2, phase 4). Flask, localhost only.

Every page is built live from reviews.db on each visit, through a connection that can't write.

    python app.py        # then open http://127.0.0.1:5000/
Stop it with Ctrl+C.

Pages (step 2 - all read-only):
    /          the theme dashboard (same page as dashboard_v2.html)
    /trends    rank after each run, reviews per day per theme, bucket size over time
    /bucket    clusters found in the bucket, and every unassigned review
    /search    search the text of every review
    /runs      every ingest run and assign run
Step 3 - read-only:
    /whatif    preview placement under another threshold; saves nothing
Step 4 - the first write:
    /review/<id>          one review: where it sits, every logged decision, and a "mark wrong" form
    POST /review/<id>/flag     save a flag (assignment_flags) - via actions.py, same as the command line
    POST /review/<id>/unflag   remove it
    /flags                every flag, with remove buttons
Step 5 - labelling (writes labels/threshold_labels.csv only, never reviews.db):
    /label                the current batch of borderline placements, one form per review
    POST /label/new       pick and save a new batch (labels/batch_<date>.csv), via labelstore
    POST /label/<log_id>  save one verdict to threshold_labels.csv, via labelstore
Writes go through write_con(): one at a time, only from this app's own pages, and paused while a
daily run is in progress.
"""
import re
import sqlite3
import threading
from flask import Flask, abort, redirect, render_template, render_template_string, request, url_for
from markupsafe import Markup, escape

import csv
import os

import actions
import charts
import config as C
import dashboard_v2
import labelstore
import queries

app = Flask(__name__)


def read_con():
    """A connection that can't write: query_only makes SQLite refuse INSERT/UPDATE/DELETE.
    reviews.db is in WAL mode, so reading never blocks the daily job's writes, and if the job
    is mid-commit the request waits up to DB_BUSY_TIMEOUT_S instead of failing."""
    con = sqlite3.connect(C.DB_PATH, timeout=C.DB_BUSY_TIMEOUT_S)
    con.execute("PRAGMA query_only = ON")
    return con


WRITE_LOCK = threading.Lock()   # the app saves one thing at a time, even with two tabs open


def write_con():
    """A normal connection, for the two flag writes only. If the daily job is mid-write, waits up to
    DB_BUSY_TIMEOUT_S, then gives up with 'database is locked' rather than hanging."""
    return sqlite3.connect(C.DB_PATH, timeout=C.DB_BUSY_TIMEOUT_S)


def from_this_app():
    """True if a form was submitted from one of this app's own pages. Stops another website open in
    the same browser from posting a flag to 127.0.0.1 behind your back."""
    ours = {f"http://{C.APP_HOST}:{C.APP_PORT}", f"http://localhost:{C.APP_PORT}"}
    origin = request.headers.get("Origin")
    if origin:
        return origin in ours
    referer = request.headers.get("Referer", "")
    return any(referer.startswith(o + "/") for o in ours)


# Messages shown after a write, chosen by key so the page never echoes text from the address bar.
MESSAGES = {
    "flagged": ("Saved: this placement is marked wrong.", False),
    "unflagged": ("Flag removed.", False),
    "notflagged": ("That decision wasn't flagged.", False),
    "busy": ("A daily run is writing to the database right now. Nothing was saved - try again in a minute.", True),
    "invalid": ("Nothing was saved: that choice isn't valid for this review.", True),
    "stale": ("Nothing was saved: this review was placed again since the page loaded. Here is its current decision.", True),
    "labelled": ("Label saved to labels/threshold_labels.csv.", False),
    "already": ("That review was already labelled; nothing was added.", True),
    "labelmissing": ("Nothing was saved: choose right or wrong, a failure type, and write a reason.", True),
    "notinbatch": ("Nothing was saved: that review isn't in the current batch.", True),
    "newbatch": ("New batch started.", False),
    "nothingleft": ("No unlabelled borderline placements left in the current runs.", True),
    "batchopen": ("Finish the current batch before starting a new one.", True),
}


def message():
    m = MESSAGES.get(request.args.get("msg", ""))
    return {"msg": m[0], "msg_bad": m[1]} if m else {"msg": None, "msg_bad": False}


def with_con(fn, *args):
    con = read_con()
    try:
        return fn(con, *args)
    finally:
        con.close()


@app.context_processor
def shared():
    """Available in every template: the dashboard's styling and the bar-chart helper."""
    return {"style": dashboard_v2.STYLE, "bars": charts.daily_bars}


@app.template_filter("highlight")
def highlight(text, q):
    """Escape the review text first, then wrap each match of q in <mark>, ignoring case."""
    safe = str(escape(text))
    if not q:
        return Markup(safe)
    return Markup(re.sub("(" + re.escape(str(escape(q))) + ")", r"<mark>\1</mark>", safe, flags=re.I))


def nav(active):
    return render_template_string('{% include "_nav.html" %}', active=active)


@app.get("/")
def dashboard():
    """The theme dashboard, same page as dashboard_v2.html but read fresh from reviews.db."""
    payload, _ = with_con(lambda con: dashboard_v2.build_payload(con, with_ids=True))
    return dashboard_v2.render_html(payload, nav=nav("/"))


@app.get("/trends")
def trends():
    """Rank after each run, reviews per day for each complaint theme, bucket size over time."""
    return render_template("trends.html", active="/trends", title="Trends",
                           intro="How the complaint themes and the unassigned bucket have moved, day by day and run by run.",
                           t=with_con(queries.trends))


@app.get("/bucket")
def bucket():
    """Clusters found in the bucket, then every unassigned review (sortable)."""
    sort = request.args.get("sort", "distance")
    if sort not in ("distance", "date", "helpful"):
        sort = "distance"
    return render_template("bucket.html", active="/bucket", title="Bucket",
                           intro="Reviews that fit no existing cluster, and the clusters found among them.",
                           b=with_con(queries.bucket, sort))


@app.get("/search")
def search():
    """Search the raw text of every review, and show where each match sits now."""
    return render_template("search.html", active="/search", title="Search",
                           intro="Every stored review, including ones never clustered.",
                           s=with_con(queries.search, request.args.get("q", "")),
                           min_chars=C.SEARCH_MIN_CHARS)


@app.get("/runs")
def runs():
    """Every ingest run (fetching reviews) and every assign run (placing them)."""
    return render_template("runs.html", active="/runs", title="Runs",
                           intro="What each daily run did, including failures and anomalies.",
                           r=with_con(queries.runs))


def _threshold(raw, default):
    """A threshold from the form, or default if blank. Raises ValueError if out of range."""
    if raw is None or raw.strip() == "":
        return default
    v = float(raw)
    if not C.WHATIF_MIN <= v <= C.WHATIF_MAX:
        raise ValueError
    return v


@app.get("/whatif")
def whatif():
    """Preview: how reviews and your labels would be placed under another threshold. Saves nothing."""
    error = None
    try:
        t = _threshold(request.args.get("t"), C.ASSIGNMENT_THRESHOLD)
        td = _threshold(request.args.get("td"), None)
    except ValueError:
        error = f"Thresholds must be numbers between {C.WHATIF_MIN} and {C.WHATIF_MAX}; showing the current setting."
        t, td = C.ASSIGNMENT_THRESHOLD, None
    return render_template("whatif.html", active="/whatif", title="What if",
                           intro="Try a different threshold and see what it would change. Nothing is saved.",
                           w=with_con(queries.whatif, t, td), error=error, lo=C.WHATIF_MIN, hi=C.WHATIF_MAX)


@app.get("/review/<review_id>")
def review(review_id):
    """One review: where it sits now, every decision logged for it, and the "mark wrong" form."""
    d = with_con(queries.review_detail, review_id)
    if d is None:
        abort(404)
    return render_template("review.html", active="", title="Review", intro=None, d=d,
                           runs=with_con(queries.run_status), stale_hours=C.RUN_STALE_HOURS,
                           note_max=C.FLAG_NOTE_MAX_CHARS, **message())


def _write(review_id, do):
    """Shared path for both writes: same-app check, run-in-progress check, one writer, short save.
    do(con, log_id) performs the write and returns a message key."""
    if not from_this_app():
        abort(403)
    try:
        log_id = int(request.form.get("log_id", ""))
    except ValueError:
        abort(400)
    back = "flags" if request.form.get("next") == "flags" else None
    with WRITE_LOCK:
        con = write_con()
        try:
            owner = con.execute("SELECT review_id, run_id FROM assignment_log WHERE log_id = ?", (log_id,)).fetchone()
            if not owner or owner[0] != review_id:
                key = "invalid"
            elif queries.run_status(con)["live"]:
                key = "busy"
            elif back is None and con.execute("SELECT run_id FROM assignments WHERE review_id = ?",
                                              (review_id,)).fetchone()[0] != owner[1]:
                key = "stale"
            else:
                key = do(con, log_id)
        except sqlite3.OperationalError:
            key = "busy"
        except actions.FlagError:
            key = "invalid"
        finally:
            con.close()
    if back:
        return redirect(url_for("flags", msg=key))
    return redirect(url_for("review", review_id=review_id, msg=key))


@app.post("/review/<review_id>/flag")
def flag(review_id):
    """Save a flag on the review's current decision, via actions.flag_assignment."""
    correct = request.form.get("correct") or None
    note = (request.form.get("note") or "").strip()[:C.FLAG_NOTE_MAX_CHARS] or None

    def do(con, log_id):
        actions.flag_assignment(con, log_id, correct, note)
        return "flagged"
    return _write(review_id, do)


@app.post("/review/<review_id>/unflag")
def unflag(review_id):
    """Remove the flag on one decision, via actions.unflag_assignment."""
    return _write(review_id, lambda con, log_id: "unflagged" if actions.unflag_assignment(con, log_id) else "notflagged")


@app.get("/flags")
def flags():
    """Every flag, newest first, with remove buttons."""
    return render_template("flags.html", active="/flags", title="Flags",
                           intro="Placements you've marked wrong.", rows=with_con(queries.flags),
                           live=bool(with_con(queries.run_status)["live"]), **message())


def _saved_labels():
    """log id -> (verdict, failure type), from threshold_labels.csv."""
    path = labelstore._path(C.THRESHOLD_LABELS_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8-sig") as f:
        return {int(r["log_id"]): (r["verdict"], r["failure_type"]) for r in csv.DictReader(f)}


@app.get("/label")
def label():
    """The current batch: one form per unlabelled review, then the ones already done."""
    path, rows = labelstore.current_batch()
    saved = _saved_labels()
    if rows:
        con = read_con()
        try:
            reset = con.execute("SELECT COALESCE(MAX(run_id), 0) FROM assign_runs WHERE status='reset'").fetchone()[0]
            for r in rows:
                hit = con.execute("SELECT review_id, run_id FROM assignment_log WHERE log_id = ?",
                                  (int(r["log_id"]),)).fetchone()
                r["review_id"], r["superseded"] = (hit[0], hit[1] < reset) if hit else (None, False)
                r["saved_verdict"], r["saved_type"] = saved.get(int(r["log_id"]), ("", ""))
        finally:
            con.close()
    return render_template("label.html", active="/label", title="Label",
                           intro="Judge borderline placements. Your verdicts set and check the threshold.",
                           path=path, path_name=os.path.basename(path) if path else None, rows=rows,
                           done_n=sum(r["done"] for r in rows), total_labelled=len(saved),
                           batch_n=C.LABELSET_WEAKEST_N + C.LABELSET_AMBIGUOUS_N,
                           failure_types=C.LABEL_FAILURE_TYPES, reason_max=C.LABEL_REASON_MAX_CHARS, **message())


@app.post("/label/new")
def label_new():
    """Pick a new batch from the current runs and save it as labels/batch_<date>.csv."""
    if not from_this_app():
        abort(403)
    with WRITE_LOCK:
        if labelstore.current_batch()[0]:
            return redirect(url_for("label", msg="batchopen"))
        con = read_con()
        try:
            reset = con.execute("SELECT COALESCE(MAX(run_id), 0) FROM assign_runs WHERE status='reset'").fetchone()[0]
            picks = labelstore.pick(con, "l.run_id > ?", (reset,), C.LABELSET_WEAKEST_N,
                                    C.LABELSET_AMBIGUOUS_N, C.LABELSET_SEED)
            if not picks:
                return redirect(url_for("label", msg="nothingleft"))
            labelstore.write_batch(con, picks)
        finally:
            con.close()
    return redirect(url_for("label", msg="newbatch"))


@app.post("/label/<int:log_id>")
def label_save(log_id):
    """Save one verdict from the current batch to threshold_labels.csv."""
    if not from_this_app():
        abort(403)
    verdict = request.form.get("verdict", "")
    failure_type = request.form.get("failure_type", "")
    reason = (request.form.get("reason") or "").strip()[:C.LABEL_REASON_MAX_CHARS]
    if verdict not in ("right", "wrong") or failure_type not in C.LABEL_FAILURE_TYPES or not reason:
        return redirect(url_for("label", msg="labelmissing") + f"#l{log_id}")
    with WRITE_LOCK:
        path, rows = labelstore.current_batch()
        row = next((r for r in rows if int(r["log_id"]) == log_id), None)
        if row is None:
            return redirect(url_for("label", msg="notinbatch"))
        con = read_con()
        try:
            ok = labelstore.record(con, log_id, verdict, reason, failure_type, row["pool"])
        except labelstore.LabelError:
            return redirect(url_for("label", msg="notinbatch"))
        finally:
            con.close()
    return redirect(url_for("label", msg="labelled" if ok else "already"))


if __name__ == "__main__":
    app.run(host=C.APP_HOST, port=C.APP_PORT, debug=False)
