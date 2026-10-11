"""
Read-only lookups for the web app's pages (v2, phase 4, step 2). Every function takes an open
connection and only runs SELECTs; app.py passes a connection that refuses writes anyway.

Cluster numbers returned here are display ids (themes_v2.json), never internal ids.
"""
import collections, json
from datetime import date, timedelta

import numpy as np

import config as C
import space as S
import taxonomy


def _taxonomy(con):
    ids = [c for (c,) in con.execute("SELECT cluster FROM centroids")]
    themes, clusters = taxonomy.load(ids)
    return themes, clusters


def _since_reset(con):
    return con.execute("SELECT COALESCE(MAX(run_id), 0) FROM assign_runs WHERE status='reset'").fetchone()[0]


def trends(con):
    """Complaint themes per day (by review date), rank per run, bucket size per run."""
    themes, clusters = _taxonomy(con)
    rows = con.execute("""SELECT a.status, a.cluster, substr(r.at, 1, 10) FROM assignments a
                          JOIN reviews_raw r USING (review_id)
                          WHERE a.status IN ('clustered', 'unassigned')""").fetchall()
    first = date.fromisoformat(min(d for *_, d in rows))
    last = date.fromisoformat(max(d for *_, d in rows))
    days = [(first + timedelta(i)).isoformat() for i in range((last - first).days + 1)]
    idx = {d: i for i, d in enumerate(days)}
    per = collections.defaultdict(lambda: [0] * len(days))
    for status, c, d in rows:
        key = clusters[c]["theme"] if status == "clustered" else "_bucket"
        per[key][idx[d]] += 1
    v2start = con.execute("""SELECT MIN(substr(c.at,1,10)) FROM assignments a JOIN reviews_clean c
                             USING (review_id) WHERE a.source != 'initial'""").fetchone()[0]

    reset = _since_reset(con)
    runs = [r for (r,) in con.execute("SELECT DISTINCT run_id FROM theme_rank_snapshots WHERE run_id >= ? "
                                      "ORDER BY run_id", (reset,))]
    def run_label(r):
        if r == reset:
            return "v1 only"
        d = con.execute("""SELECT MAX(substr(c.at,1,10)) FROM assignment_log l JOIN reviews_clean c
                           USING (review_id) WHERE l.run_id = ?""", (r,)).fetchone()[0]
        return f"to {d[5:]}" if d else f"run {r}"
    snaps = collections.defaultdict(dict)
    for r, k, rank, total in con.execute("SELECT run_id, theme, rank, total FROM theme_rank_snapshots "
                                         "WHERE run_id >= ?", (reset,)):
        snaps[k][r] = (rank, total)
    bucket_by_run = dict(con.execute("SELECT run_id, total FROM rank_snapshots WHERE grp='bucket' "
                                     "AND run_id >= ?", (reset,)))

    comp = [t for t in themes.values() if t["kind"] == "complaint"]
    latest = runs[-1]
    comp.sort(key=lambda t: snaps[t["key"]][latest][0])
    return {
        "days": days, "v2start": v2start,
        "themes": [{"key": t["key"], "name": t["name"], "daily": per[t["key"]],
                    "total": sum(per[t["key"]]), "ranks": [snaps[t["key"]].get(r, (None, None))[0] for r in runs]}
                   for t in comp],
        "bucket_daily": per["_bucket"],
        "runs": [{"id": r, "label": run_label(r), "bucket": bucket_by_run.get(r)} for r in runs],
    }


def bucket(con, sort="distance"):
    """Unassigned reviews with their nearest cluster, plus each cluster found in the bucket."""
    themes, clusters = _taxonomy(con)
    tname = {k: t["name"] for k, t in themes.items()}
    order = {"distance": "a.nearest_distance DESC", "date": "r.at DESC", "helpful": "r.thumbs_up DESC"}[sort]
    unassigned = [{"id": rid, "nearest": clusters[c]["id"], "nearest_theme": tname[clusters[c]["theme"]],
                   "distance": d, "stars": s, "date": at[:10], "helpful": u or 0, "text": t}
                  for rid, c, d, s, at, u, t in con.execute(f"""
                      SELECT a.review_id, a.nearest_cluster, a.nearest_distance, r.score, r.at, r.thumbs_up, r.content
                      FROM assignments a JOIN reviews_raw r USING (review_id)
                      WHERE a.status = 'unassigned' ORDER BY {order}""")]

    ids, cents, ns = S.load_centroids(con)
    found = []
    for i in themes[taxonomy.UNMAPPED_THEME]["clusters"]:
        c = next(k for k, v in clusters.items() if v["id"] == i)
        rows = con.execute("""SELECT r.score, substr(r.at,1,10), COALESCE(r.thumbs_up,0), a.distance, r.content
                              FROM assignments a JOIN reviews_raw r USING (review_id)
                              WHERE a.cluster = ? AND a.status = 'clustered'""", (c,)).fetchall()
        D = S.distances(cents[[ids.index(c)]], cents)[0]
        near = sorted((D[k], x) for k, x in enumerate(ids) if x != c)[:C.CARD_NEAREST_N]
        stars = collections.Counter(r[0] for r in rows)
        created_run = con.execute("SELECT created_run FROM centroids WHERE cluster = ?", (c,)).fetchone()[0]
        found.append({
            "id": i, "size": len(rows), "size_at_creation": ns[c], "created_run": created_run,
            "mean": float(np.mean([r[0] for r in rows])),
            "stars": [stars.get(k, 0) for k in range(1, 6)],
            "first": min(r[1] for r in rows), "last": max(r[1] for r in rows),
            "radius": float(np.median([r[3] for r in rows])),
            "nearest": [{"id": clusters[x]["id"], "theme": tname[clusters[x]["theme"]], "distance": float(d)}
                        for d, x in near],
            "typical": [{"stars": r[0], "date": r[1], "helpful": r[2], "text": r[4]}
                        for r in sorted(rows, key=lambda r: r[3])[:C.BUCKET_TYPICAL_N]],
        })
    found.sort(key=lambda f: -f["size"])
    return {"unassigned": unassigned, "found": found, "sort": sort}


def search(con, q):
    """Reviews whose raw text contains q (case-insensitive), with where each one sits now."""
    q = (q or "").strip()
    if len(q) < C.SEARCH_MIN_CHARS:
        return {"q": q, "results": [], "total": 0, "too_short": bool(q)}
    themes, clusters = _taxonomy(con)
    tname = {k: t["name"] for k, t in themes.items()}
    like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    where = "FROM reviews_raw r LEFT JOIN assignments a USING (review_id) WHERE r.content LIKE ? ESCAPE '\\'"
    total = con.execute(f"SELECT COUNT(*) {where}", (like,)).fetchone()[0]
    results = []
    for rid, s, at, u, t, status, c in con.execute(
            f"SELECT r.review_id, r.score, r.at, r.thumbs_up, r.content, a.status, a.cluster {where} "
            f"ORDER BY r.thumbs_up DESC, r.at DESC LIMIT ?", (like, C.SEARCH_MAX_RESULTS)):
        if status == "clustered":
            where_now = f"{tname[clusters[c]['theme']]} · sub-theme {clusters[c]['id']}"
        elif status == "unassigned":
            where_now = "Unassigned bucket"
        elif status == "noise":
            where_now = "v1 noise (never clustered)"
        else:
            where_now = "Not clustered (under 5 words or duplicate text)"
        results.append({"id": rid, "stars": s, "date": at[:10], "helpful": u or 0, "text": t,
                        "where": where_now, "linkable": status in ("clustered", "unassigned")})
    return {"q": q, "results": results, "total": total, "too_short": False}


def runs(con):
    """Every ingest run and every assign run, newest first."""
    ingest = [dict(zip(("id", "started", "finished", "status", "since", "newest", "fetched", "inserted",
                        "clean_inserted", "per_day", "anomalous", "anomaly", "error"), r))
              for r in con.execute("""SELECT run_id, started_at, finished_at, status, watermark_before,
                                      watermark_after, fetched, inserted, clean_inserted, per_day,
                                      anomalous, anomaly_reason, error
                                      FROM ingest_runs ORDER BY run_id DESC""")]
    assign = [dict(zip(("id", "started", "status", "threshold", "pending", "assigned", "unassigned",
                        "bucket_after", "new_clusters", "error"), r))
              for r in con.execute("""SELECT run_id, started_at, status, threshold, pending, assigned,
                                      unassigned, bucket_after, new_clusters, error
                                      FROM assign_runs ORDER BY run_id DESC""")]
    return {"ingest": ingest, "assign": assign, "current_from": _since_reset(con)}


def _read_labels(path):
    """A label file saved by the pipeline (UTF-8 CSV) or by Excel as 'Unicode text' (UTF-16, tabs)."""
    import csv, io
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return list(csv.DictReader(io.StringIO(raw.decode("utf-16")), delimiter="\t"))
    return list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))


def whatif(con, t, td=None):
    """Preview of placement under a global threshold t, and optionally td for the sub-themes in
    WHATIF_OVERRIDE_DISPLAY_IDS. Compared with the same calculation at the current threshold.

    Method: every review placed since v1 is compared again with the 30 frozen v1 centroids, from
    its stored vector. Clusters found in the bucket are left out on purpose - they only exist
    because of what the bucket held at 0.58, and bucket re-clustering is not simulated.
    Nothing is written."""
    themes, clusters = _taxonomy(con)
    rows = con.execute("SELECT cluster, vec FROM centroids WHERE origin = 'initial' ORDER BY cluster").fetchall()
    ids = [r[0] for r in rows]
    cents = np.stack([S.from_blob(r[1]) for r in rows])
    disp = {c: clusters[c]["id"] for c in ids}
    theme_of = {c: clusters[c]["theme"] for c in ids}
    override = {c for c in ids if disp[c] in C.WHATIF_OVERRIDE_DISPLAY_IDS}

    def limit(c, g, o):
        return o if (o is not None and c in override) else g

    new = con.execute("""SELECT a.review_id, v.red FROM assignments a JOIN review_vectors v USING (review_id)
                         WHERE a.source != 'initial'""").fetchall()
    D = S.distances(np.stack([S.from_blob(v) for _, v in new]), cents)
    near = [ids[k] for k in D.argmin(1)]
    dist = D.min(1)

    base_t = C.ASSIGNMENT_THRESHOLD
    agg = {k: {"n": 0, "base": 0, "set": 0} for k in themes}
    for c, d in zip(near, dist):
        a = agg[theme_of[c]]
        a["n"] += 1
        a["base"] += d < base_t
        a["set"] += d < limit(c, t, td)
    order = [k for k in themes if themes[k]["kind"] == "complaint"] + \
            [k for k in themes if themes[k]["kind"] in ("separate", "positive")]
    table = []
    for k in order:
        a = agg[k]
        if not a["n"]:
            continue
        table.append({"name": themes[k]["name"], "kind": themes[k]["kind"], "n": a["n"],
                      "base_placed": a["base"], "base_bucket": a["n"] - a["base"],
                      "set_placed": a["set"], "set_bucket": a["n"] - a["set"],
                      "override": any(disp[c] in C.WHATIF_OVERRIDE_DISPLAY_IDS for c in ids if theme_of[c] == k)})
    tot = {x: sum(r[x] for r in table) for x in ("n", "base_placed", "base_bucket", "set_placed", "set_bucket")}

    def vec(review_id):
        v = S.from_blob(con.execute("SELECT red FROM review_vectors WHERE review_id = ?", (review_id,)).fetchone()[0])
        d = S.distances(v[None], cents)[0]
        k = int(d.argmin())
        return ids[k], float(d[k])

    def outcome_threshold(right, placed):
        return {(True, True): "right, placed", (False, True): "WRONG, placed",
                (True, False): "right, lost to bucket", (False, False): "wrong, kept out"}[(right, placed)]

    def outcome_purity(yes, placed, into_yes_theme):
        if not placed:
            return "delivery complaint left in bucket" if yes else "not placed (right home unknown)"
        if into_yes_theme:
            return "right, placed in Delivery" if yes else "WRONG, placed in Delivery"
        return "WRONG, placed outside Delivery" if yes else "placed elsewhere (can't tell)"

    labels = []
    for r in _read_labels(C.THRESHOLD_LABELS_FILE):
        c, d = vec(r["review_id"])
        right = r["verdict"].strip().lower() == "right"
        pb, ps = d < base_t, d < limit(c, t, td)
        labels.append({"source": "threshold", "id": r["review_id"], "verdict": r["verdict"].strip().lower(), "text": r["text"],
                       "cluster": disp[c], "theme": themes[theme_of[c]]["name"], "distance": d,
                       "base": outcome_threshold(right, pb), "set": outcome_threshold(right, ps)})
    for src in C.PURITY_LABEL_SOURCES:
        for r in _read_labels(src["file"]):
            v = r["verdict"].strip().lower()
            if v not in ("yes", "no"):
                continue
            c, d = vec(r["review_id"])
            yes, into = v == "yes", theme_of[c] == src["yes_theme"]
            labels.append({"source": f"cluster {src['cluster']} purity", "id": r["review_id"], "verdict": v, "text": r["text"],
                           "cluster": disp[c], "theme": themes[theme_of[c]]["name"], "distance": d,
                           "base": outcome_purity(yes, d < base_t, into),
                           "set": outcome_purity(yes, d < limit(c, t, td), into)})

    # threshold labels and purity labels answer different questions, so they're counted apart
    purity_keys = {
        "right, placed in Delivery": "yes, placed in Delivery (right)",
        "WRONG, placed outside Delivery": "yes, placed outside Delivery (wrong)",
        "delivery complaint left in bucket": "yes, left in bucket",
        "WRONG, placed in Delivery": "no, placed in Delivery (wrong)",
        "placed elsewhere (can't tell)": "no, placed elsewhere (can't tell)",
        "not placed (right home unknown)": "no, left in bucket (can't tell)",
    }
    threshold_keys = {"right, placed": "right, placed", "WRONG, placed": "wrong, placed",
                      "right, lost to bucket": "right, lost to bucket", "wrong, kept out": "wrong, kept out"}

    def summary(key):
        out = {"threshold": collections.Counter(), "purity": collections.Counter()}
        for x in labels:
            if x["source"] == "threshold":
                out["threshold"][threshold_keys[x[key]]] += 1
            else:
                out["purity"][purity_keys[x[key]]] += 1
        return out

    radius = {}
    for c in override:
        ds = [d for (d,) in con.execute("SELECT distance FROM assignments WHERE source='initial' AND cluster=?", (c,))]
        radius[disp[c]] = float(np.percentile(ds, 95))

    return {"t": t, "td": td, "base_t": base_t, "override_ids": list(C.WHATIF_OVERRIDE_DISPLAY_IDS),
            "override_p95": dict(sorted(radius.items())), "table": table, "total": tot,
            "labels": labels, "sum_base": summary("base"), "sum_set": summary("set"),
            "threshold_keys": list(threshold_keys.values()), "purity_keys": list(purity_keys.values()),
            "n_threshold": sum(x["source"] == "threshold" for x in labels),
            "n_purity": sum(x["source"] != "threshold" for x in labels),
            "purity_sources": C.PURITY_LABEL_SOURCES}



def run_status(con):
    """Runs still marked 'running'. 'live' started within RUN_STALE_HOURS and may really be going,
    so the app holds off writing; 'stale' are older - stopped without recording - and only warned about."""
    from datetime import datetime, timedelta
    cutoff = (datetime.now() - timedelta(hours=C.RUN_STALE_HOURS)).isoformat()
    out = {"live": [], "stale": []}
    for table, kind in (("ingest_runs", "ingest"), ("assign_runs", "assign")):
        for run_id, started in con.execute(f"SELECT run_id, started_at FROM {table} WHERE status = 'running'"):
            out["live" if started >= cutoff else "stale"].append(
                {"kind": kind, "id": run_id, "started": started[:16].replace("T", " ")})
    return out


def review_detail(con, review_id):
    """One review: its text, where it sits now, every decision logged for it, and its flag."""
    themes, clusters = _taxonomy(con)
    tname = {k: t["name"] for k, t in themes.items()}
    r = con.execute("SELECT content, score, at, thumbs_up FROM reviews_raw WHERE review_id = ?",
                    (review_id,)).fetchone()
    if not r:
        return None

    def label(c):
        return f"{clusters[c]['id']} · {tname[clusters[c]['theme']]}" if c in clusters else f"internal {c}"

    out = {"id": review_id, "text": r[0], "stars": r[1], "date": r[2][:10], "helpful": r[3] or 0,
           "placement": None, "current": None, "history": [], "flag": None, "options": [], "why_not": None}
    a = con.execute("""SELECT status, cluster, distance, nearest_cluster, nearest_distance, second_cluster,
                              second_distance, source, run_id FROM assignments WHERE review_id = ?""",
                    (review_id,)).fetchone()
    if a is None:
        out["why_not"] = "Never clustered: under 5 words or duplicate text, so there is no placement to flag."
        return out
    status, c, dist, n1, d1, n2, d2, source, run_id = a
    where = (label(c) if status == "clustered" else
             "Unassigned bucket" if status == "unassigned" else "v1 noise (never clustered)")
    out["placement"] = {"status": status, "source": source, "run": run_id, "where": where, "distance": dist,
                        "nearest": label(n1) if n1 is not None else None, "nearest_d": d1,
                        "second": label(n2) if n2 is not None else None, "second_d": d2}
    reset = _since_reset(con)
    for log_id, rid, logged, event, outcome, lc, ld, ln1d, thr, metric in con.execute(
            """SELECT log_id, run_id, logged_at, event, outcome, cluster, distance, nearest_distance,
                      threshold, metric FROM assignment_log WHERE review_id = ? ORDER BY log_id DESC""",
            (review_id,)):
        out["history"].append({"log_id": log_id, "run": rid, "when": logged[:16].replace("T", " "),
                               "event": event, "outcome": outcome,
                               "where": label(lc) if outcome == "assigned" else "bucket",
                               "distance": ld if ld is not None else ln1d, "threshold": thr, "metric": metric,
                               "superseded": rid < reset or rid != run_id})
    if source == "initial":
        out["why_not"] = ("Placed by v1's original clustering, not by a daily decision, so there is no decision "
                          "to flag. Flags apply to daily placements only.")
        return out
    cur = next((h for h in out["history"] if h["run"] == run_id), None)
    out["current"] = cur
    if cur:
        f = con.execute("SELECT flagged_at, correct_cluster, note FROM assignment_flags WHERE log_id = ?",
                        (cur["log_id"],)).fetchone()
        if f:
            cc = f[1]
            out["flag"] = {"when": f[0][:16].replace("T", " "), "note": f[2],
                           "correct": None if cc is None else "Unassigned bucket" if cc == "bucket" else label(int(cc))}
    out["options"] = [{"value": c2, "label": f"{v['id']} · {tname[v['theme']]}"
                                             + (f" - {v['name']}" if v["name"] != tname[v["theme"]] else "")}
                      for c2, v in sorted(clusters.items(), key=lambda kv: kv[1]["id"])]
    return out


def flags(con):
    """Every flag, newest first, with the review and the decision it marks wrong."""
    themes, clusters = _taxonomy(con)
    tname = {k: t["name"] for k, t in themes.items()}

    def label(c):
        return f"{clusters[c]['id']} · {tname[clusters[c]['theme']]}" if c in clusters else f"internal {c}"

    reset = _since_reset(con)
    rows = []
    for log_id, rid, when, cc, note, run, outcome, lc, ld, ln1d, text, stars in con.execute(
            """SELECT f.log_id, f.review_id, f.flagged_at, f.correct_cluster, f.note, l.run_id, l.outcome,
                      l.cluster, l.distance, l.nearest_distance, r.content, r.score
               FROM assignment_flags f JOIN assignment_log l USING (log_id)
               JOIN reviews_raw r ON r.review_id = f.review_id ORDER BY f.flagged_at DESC"""):
        rows.append({"log_id": log_id, "id": rid, "when": when[:16].replace("T", " "), "note": note,
                     "was": label(lc) if outcome == "assigned" else "Unassigned bucket",
                     "distance": ld if ld is not None else ln1d,
                     "correct": None if cc is None else "Unassigned bucket" if cc == "bucket" else label(int(cc)),
                     "text": text, "stars": stars, "old": run < reset})
    return rows
