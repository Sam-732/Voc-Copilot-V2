"""
Hand labels on borderline assignments. Storage is files only (Samprat, option A, 11 Oct 2026):
labels/threshold_labels.csv is the one record of verdicts, and is kept in git.

Shared by labels/record_label.py and assignlog.py (command line) and app.py (web app), so a label
is written, and a batch is picked, exactly one way.

The record keeps the numbers as they were when the verdict was given (metric, threshold,
distances), so it stays meaningful after the threshold or metric changes. A log id that's
already recorded is skipped, never duplicated.

A batch is a CSV of borderline decisions to label (labels/batch_<date>.csv): picked by
pick(), written by write_batch(). Fill it in by hand and import it with record_label.py
--from-csv, or label it in the app - either way the verdicts land in threshold_labels.csv.
"""
import csv, glob, json, os, random
from datetime import datetime

import numpy as np

import config as C
import taxonomy

REPO = os.path.dirname(os.path.abspath(__file__))

FIELDS = ["labelled_at", "log_id", "pool", "metric", "threshold_at_label", "stars",
          "cluster", "cluster_display_id", "cluster_label", "distance",
          "second_cluster", "second_display_id", "second_label",
          "second_distance", "gap", "verdict", "failure_type", "reasoning", "review_id", "text"]

BATCH_FIELDS = ["log_id", "pool", "stars", "text", "cluster", "cluster_display_id", "cluster_name",
                "distance", "second_cluster", "second_display_id", "second_name", "second_distance",
                "verdict", "reason"]


class LabelError(ValueError):
    pass


def _path(p):
    return p if os.path.isabs(p) else os.path.join(REPO, p)


def recorded():
    """log ids already in threshold_labels.csv."""
    path = _path(C.THRESHOLD_LABELS_FILE)
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8-sig") as f:
        return {int(r["log_id"]) for r in csv.DictReader(f)}


def record(con, log_id, verdict, reasoning, failure_type="", pool=""):
    """Append one verdict to threshold_labels.csv. Returns False if the log id is already recorded."""
    verdict = (verdict or "").strip().lower()
    if verdict not in ("right", "wrong"):
        raise LabelError(f"log {log_id}: verdict must be right or wrong, got {verdict!r}")
    if log_id in recorded():
        return False
    r = con.execute("""SELECT l.metric, l.threshold, c.score, l.cluster, l.cluster_label, l.distance,
                       l.second_cluster, l.second_label, l.second_distance, l.gap, l.review_id, c.text
                       FROM assignment_log l JOIN reviews_clean c USING (review_id)
                       WHERE l.log_id = ?""", (log_id,)).fetchone()
    if not r:
        raise LabelError(f"no assignment decision with log id {log_id}")
    _, clusters = taxonomy.load([x for x in (r[3], r[6]) if x is not None])
    disp = lambda c: clusters[c]["id"] if c is not None else ""
    path = _path(C.THRESHOLD_LABELS_FILE)
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(FIELDS)
        w.writerow([datetime.now().isoformat(timespec="seconds"), log_id, pool or "", *r[:4], disp(r[3]), *r[4:6],
                    r[6], disp(r[6]), *r[7:10], verdict, failure_type or "", reasoning or "", r[10], r[11]])
    return True


def pick(con, where, params, weakest, ambiguous, seed):
    """Borderline placed decisions to label. Weakest: evenly spaced across the top distance
    quantile (LABELSET_WEAK_PERCENTILE). Ambiguous: smallest nearest-vs-second gaps. Skips anything
    already recorded. Shuffled, so position doesn't hint at the pool. Returns [(log_id, pool)]."""
    skip = recorded()
    rows = [r for r in con.execute(f"""SELECT l.log_id, l.distance, l.gap FROM assignment_log l
                                       WHERE {where} AND l.outcome = 'assigned'""", params)
            if r[0] not in skip]
    if not rows:
        return []
    cut = np.percentile([r[1] for r in rows], C.LABELSET_WEAK_PERCENTILE)
    top = sorted((r for r in rows if r[1] >= cut), key=lambda r: r[1])
    k = min(weakest, len(top))
    weak = [top[int(i)] for i in np.linspace(0, len(top) - 1, k)] if k else []
    taken = {r[0] for r in weak}
    amb = [r for r in sorted(rows, key=lambda r: r[2]) if r[0] not in taken][:ambiguous]
    picks = [(r[0], "weakest") for r in weak] + [(r[0], "ambiguous") for r in amb]
    random.Random(seed).shuffle(picks)
    return picks


def write_batch(con, picks, out=None):
    """Write picks to a batch CSV (empty verdict and reason columns). Returns its path."""
    wn_path = _path(os.path.join(C.LABELS_DIR, "working_names.json"))
    working = json.load(open(wn_path, encoding="utf-8"))["names"] if os.path.exists(wn_path) else {}

    def name(cluster, label):
        if str(cluster) in working and "unnamed" in (label or ""):
            return f"UNNAMED - working name: {working[str(cluster)]}"
        return label
    if out is None:   # never overwrite an earlier batch, even one started in the same second
        stem = _path(os.path.join(C.LABELS_DIR, f"batch_{datetime.now():%Y%m%d_%H%M%S}"))
        out, n = stem + ".csv", 2
        while os.path.exists(out):
            out, n = f"{stem}_{n}.csv", n + 1
    rows = []
    for lid, pool in picks:
        r = con.execute("""SELECT c.score, c.text, l.cluster, l.cluster_label, ROUND(l.distance, 4),
                           l.second_cluster, l.second_label, ROUND(l.second_distance, 4)
                           FROM assignment_log l JOIN reviews_clean c USING (review_id)
                           WHERE l.log_id = ?""", (lid,)).fetchone()
        if not r:
            raise LabelError(f"no assignment decision with log id {lid}")
        score, text, c1, l1, d1, c2, l2, d2 = r
        _, tx = taxonomy.load([c1, c2])
        rows.append([lid, pool, score, text, c1, tx[c1]["id"], name(c1, l1), d1,
                     c2, tx[c2]["id"], name(c2, l2), d2, "", ""])
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(BATCH_FIELDS)
        w.writerows(rows)
    return out


def current_batch():
    """The newest batch_*.csv that still has unlabelled rows, as (path, rows); each row gets
    'done' = already in threshold_labels.csv. (None, []) if every batch is finished."""
    done = recorded()
    files = sorted(glob.glob(_path(os.path.join(C.LABELS_DIR, "batch_*.csv"))), key=os.path.getmtime, reverse=True)
    for path in files:
        with open(path, encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        for r in rows:
            r["done"] = int(r["log_id"]) in done
        if rows and not all(r["done"] for r in rows):
            return path, rows
    return None, []
