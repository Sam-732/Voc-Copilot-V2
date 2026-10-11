"""
Read the assignment log and record where it's wrong (v2, phase 3).

By default every command looks at the "current" runs: those since the last `assign.py --reset`.
    --run N   one run only
    --all     every run ever logged, including before resets

    python assignlog.py export [--run N | --all] [--out file.csv]
    python assignlog.py weakest   [-n 20]   assigned reviews furthest from their centroid
    python assignlog.py ambiguous [-n 20]   assigned reviews with the smallest nearest-vs-2nd gap
    python assignlog.py flag LOG_ID [--correct CLUSTER|bucket] [--note "..."]
    python assignlog.py unflag LOG_ID
    python assignlog.py flags [--out file.csv]   every assignment marked wrong
    python assignlog.py labelset [--weakest 5] [--ambiguous 5] [--log-ids 1,2] [--out file.csv]
        a batch to hand-label: CSV with empty verdict/reason columns, saved in labels/.
        Skips reviews already in labels/threshold_labels.csv. Read it back with
        python labels/record_label.py --from-csv FILE
    python assignlog.py purityset --cluster N --question "..." [-n 10]   random members to hand-check
    python assignlog.py card --cluster N                                 one-page summary of a cluster
    (--cluster takes the display id shown on the dashboard)

weakest/ambiguous take --include-unassigned to also show bucket decisions.
The first column in the terminal output is LOG_ID - that's what `flag` takes.
"""
import argparse, collections, csv, json, os, sqlite3, sys
from datetime import datetime

import actions
import config as C
import space as S
import taxonomy

COLUMNS = """l.log_id, l.run_id, l.logged_at, l.review_id, c.at AS review_date, c.score AS stars,
    l.event, l.outcome, l.cluster, l.cluster_label, ROUND(l.distance, 6) AS distance,
    l.nearest_cluster, l.nearest_label, ROUND(l.nearest_distance, 6) AS nearest_distance,
    l.second_cluster, l.second_label, ROUND(l.second_distance, 6) AS second_distance,
    ROUND(l.gap, 6) AS gap, l.threshold,
    CASE WHEN f.log_id IS NULL THEN '' ELSE 'WRONG' END AS flagged,
    f.correct_cluster, f.note AS flag_note, c.text"""

BASE = f"""SELECT {COLUMNS} FROM assignment_log l
    JOIN reviews_clean c USING (review_id)
    LEFT JOIN assignment_flags f ON f.log_id = l.log_id"""


def scope(con, args):
    if getattr(args, "run", None) is not None:
        return "l.run_id = ?", (args.run,), f"run {args.run}"
    if getattr(args, "all", False):
        return "1=1", (), "all runs"
    last = con.execute("SELECT COALESCE(MAX(run_id), 0) FROM assign_runs WHERE status='reset'").fetchone()[0]
    return "l.run_id > ?", (last,), "current runs (since last reset)"


def write_csv(cur, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    names = [d[0] for d in cur.description]
    rows = cur.fetchall()
    with open(path, "w", newline="", encoding="utf-8-sig") as f:   # BOM so Excel reads Hindi/Tamil
        w = csv.writer(f)
        w.writerow(names)
        w.writerows(rows)
    return len(rows)


def show(rows, names):
    r = [dict(zip(names, x)) for x in rows]
    print(f"{'log_id':>6} {'run':>3} {'dist':>8} {'gap':>8} {'*':>1}  {'flag':5} assigned -> 2nd nearest")
    for x in r:
        where = (f"{x['cluster']} {x['cluster_label'][:40]}" if x["outcome"] == "assigned"
                 else f"BUCKET (nearest {x['nearest_cluster']} {x['nearest_label'][:30]})")
        second = f"{x['second_cluster']} {x['second_label'][:30]}"
        print(f"{x['log_id']:>6} {x['run_id']:>3} {x['distance'] if x['distance'] is not None else x['nearest_distance']:>8.5f} "
              f"{x['gap']:>8.5f} {x['stars']:>1}  {x['flagged']:5} {where}  |  2nd: {second}")
        print(f"{'':>12}{x['text'][:C.TEXT_PREVIEW_CHARS]}")
    print(f"\n{len(r)} rows. Mark one wrong with: python assignlog.py flag LOG_ID --correct CLUSTER|bucket --note \"...\"")


def cmd_export(con, args):
    where, params, label = scope(con, args)
    cur = con.execute(f"{BASE} WHERE {where} ORDER BY l.log_id", params)
    tag = f"run{args.run}" if args.run is not None else ("all" if args.all else "current")
    out = args.out or os.path.join(C.EXPORT_DIR, f"assignments_{tag}.csv")
    n = write_csv(cur, out)
    print(f"wrote {n} log rows ({label}) to {out}")


def cmd_ranked(con, args, order, title):
    where, params, label = scope(con, args)
    if not args.include_unassigned:
        where += " AND l.outcome = 'assigned'"
    cur = con.execute(f"{BASE} WHERE {where} ORDER BY {order} LIMIT ?", (*params, args.n))
    print(f"--- {title} - {label} ---")
    show(cur.fetchall(), [d[0] for d in cur.description])


def cmd_flag(con, args):
    try:
        row = actions.flag_assignment(con, args.log_id, args.correct, args.note)
    except actions.FlagError as e:
        sys.exit(str(e))
    got = f"cluster {row['cluster']}" if row["outcome"] == "assigned" else "bucket"
    print(f"flagged log {args.log_id} as wrong (was {got}"
          f"{', should be ' + args.correct if args.correct else ''})")


def cmd_unflag(con, args):
    gone = actions.unflag_assignment(con, args.log_id)
    print(f"removed flag on log {args.log_id}" if gone else f"log {args.log_id} was not flagged")


def cmd_flags(con, args):
    cur = con.execute(f"{BASE} WHERE f.log_id IS NOT NULL ORDER BY f.flagged_at")
    if args.out:
        print(f"wrote {write_csv(cur, args.out)} flagged rows to {args.out}")
    else:
        show(cur.fetchall(), [d[0] for d in cur.description])


LABEL_FIELDS = ["log_id", "pool", "stars", "text", "cluster", "cluster_display_id", "cluster_name",
                "distance", "second_cluster", "second_display_id", "second_name", "second_distance",
                "verdict", "reason"]


def already_labelled():
    path = os.path.join(C.LABELS_DIR, "threshold_labels.csv")
    if not os.path.exists(path):
        return set()
    with open(path, encoding="utf-8-sig") as f:
        return {int(r["log_id"]) for r in csv.DictReader(f)}


def pick_labelset(con, args, where, params):
    """Weakest: evenly spaced across the top quantile of distance. Ambiguous: smallest gaps.
    Skips anything already in labels/threshold_labels.csv. Shuffled."""
    import random
    import numpy as np
    skip = already_labelled()
    rows = [r for r in con.execute(f"""SELECT l.log_id, l.distance, l.gap FROM assignment_log l
                                       WHERE {where} AND l.outcome = 'assigned'""", params)
            if r[0] not in skip]
    if not rows:
        return []
    cut = np.percentile([r[1] for r in rows], C.LABELSET_WEAK_PERCENTILE)
    top = sorted((r for r in rows if r[1] >= cut), key=lambda r: r[1])
    k = min(args.weakest, len(top))
    weak = [top[int(i)] for i in np.linspace(0, len(top) - 1, k)] if k else []
    taken = {r[0] for r in weak}
    amb = [r for r in sorted(rows, key=lambda r: r[2]) if r[0] not in taken][:args.ambiguous]
    picks = [(r[0], "weakest") for r in weak] + [(r[0], "ambiguous") for r in amb]
    random.Random(args.seed).shuffle(picks)
    return picks


def cmd_labelset(con, args):
    import json
    if args.log_ids:
        pools = {}
        session = os.path.join(C.LABELS_DIR, "_session.json")
        if os.path.exists(session):
            pools = dict(map(tuple, json.load(open(session))["picks"]))
        picks = [(int(i), pools.get(int(i), "manual")) for i in args.log_ids.split(",")]
    else:
        where, params, _ = scope(con, args)
        picks = pick_labelset(con, args, where, params)
    if not picks:
        sys.exit("nothing to export")
    wn_path = os.path.join(C.LABELS_DIR, "working_names.json")
    working = json.load(open(wn_path, encoding="utf-8"))["names"] if os.path.exists(wn_path) else {}
    def name(cluster, label):
        if str(cluster) in working and "unnamed" in (label or ""):
            return f"UNNAMED - working name: {working[str(cluster)]}"
        return label
    out = args.out or os.path.join(C.LABELS_DIR, f"batch_{datetime.now():%Y%m%d_%H%M%S}.csv")
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(LABEL_FIELDS)
        for lid, pool in picks:
            r = con.execute("""SELECT c.score, c.text, l.cluster, l.cluster_label, ROUND(l.distance, 4),
                               l.second_cluster, l.second_label, ROUND(l.second_distance, 4)
                               FROM assignment_log l JOIN reviews_clean c USING (review_id)
                               WHERE l.log_id = ?""", (lid,)).fetchone()
            if not r:
                sys.exit(f"no log row {lid}")
            score, text, c1, l1, d1, c2, l2, d2 = r
            _, tx = taxonomy.load([c1, c2])
            w.writerow([lid, pool, score, text, c1, tx[c1]["id"], name(c1, l1), d1,
                        c2, tx[c2]["id"], name(c2, l2), d2, "", ""])
    print(f"wrote {len(picks)} reviews to label: {out}")
    print(f"fill in verdict (right/wrong) and reason, then: python labels/record_label.py --from-csv {out}")


def internal_id(con, display_id):
    ids = [c for (c,) in con.execute("SELECT cluster FROM centroids")]
    _, cl = taxonomy.load(ids)
    hit = [c for c, v in cl.items() if v["id"] == display_id]
    if not hit:
        sys.exit(f"no cluster with display id {display_id}")
    return hit[0], cl


def cmd_purityset(con, args):
    """Random members of one cluster, to hand-check what fraction truly share one complaint."""
    import random
    c, cl = internal_id(con, args.cluster)
    rows = con.execute("""SELECT a.review_id, r.score, substr(r.at,1,10), COALESCE(r.thumbs_up,0), a.source, r.content
                          FROM assignments a JOIN reviews_raw r USING (review_id)
                          WHERE a.cluster = ? AND a.status = 'clustered' ORDER BY a.review_id""", (c,)).fetchall()
    pick = random.Random(args.seed).sample(rows, min(args.n, len(rows)))
    out = args.out or os.path.join(C.LABELS_DIR, f"purity_cluster{args.cluster}.csv")
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["review_id", "cluster_display_id", "cluster_internal_id", "stars", "date", "helpful",
                    "source", "text", "question", "verdict", "reason"])
        for rid, sc, d, u, src, t in pick:
            w.writerow([rid, args.cluster, c, sc, d, u, src, t, args.question, "", ""])
    print(f"wrote {len(pick)} of {len(rows)} members of cluster {args.cluster} (internal {c}) to {out}")
    print("verdict: yes / no per row")


def cmd_card(con, args):
    """One-page markdown summary of a cluster, for deciding what it is."""
    import numpy as np
    c, cl = internal_id(con, args.cluster)
    themes = json.load(open(C.THEMES_V2_FILE, encoding="utf-8"))["themes"]
    tname = {t["key"]: t["name"] for t in themes}
    tname[taxonomy.UNMAPPED_THEME] = tname.get(taxonomy.UNMAPPED_THEME, "Found in the bucket")
    rows = con.execute("""SELECT r.score, substr(r.at,1,10), COALESCE(r.thumbs_up,0), a.source, a.distance, r.content
                          FROM assignments a JOIN reviews_raw r USING (review_id)
                          WHERE a.cluster = ? AND a.status = 'clustered'""", (c,)).fetchall()
    ids, cents, ns = S.load_centroids(con)
    D = S.distances(cents[[ids.index(c)]], cents)[0]
    near = sorted((D[i], x) for i, x in enumerate(ids) if x != c)[:C.CARD_NEAREST_N]
    stars = collections.Counter(r[0] for r in rows)
    dates = sorted(r[1] for r in rows)
    weeks = collections.Counter(dates)
    first_seen = con.execute("SELECT created_at FROM centroids WHERE cluster=?", (c,)).fetchone()[0]
    L = [f"# Cluster {args.cluster} (internal {c})", "",
         f"- Theme now: {tname[cl[c]['theme']]} - name: {cl[c]['name']}",
         f"- Reviews: {len(rows)} (at creation: {ns[c]}); centroid created {first_seen[:10]}",
         f"- Stars: " + ", ".join(f"{k}* {stars.get(k,0)}" for k in range(1, 6)) +
         f" (mean {np.mean([r[0] for r in rows]):.2f})",
         f"- Review dates: {dates[0]} to {dates[-1]}; per day: " + ", ".join(f"{d[5:]} {n}" for d, n in sorted(weeks.items())),
         f"- Helpful votes: {sum(r[2] for r in rows)} total, top {max(r[2] for r in rows)}",
         f"- Nearest other clusters ({C.DISTANCE_METRIC} between centroids): " +
         "; ".join(f"{cl[x]['id']} {tname[cl[x]['theme']]} {d:.2f}" for d, x in near), "",
         f"## {C.CARD_TYPICAL_N} most typical (closest to centroid)", ""]
    for sc, d, u, src, dist, t in sorted(rows, key=lambda r: r[4])[:C.CARD_TYPICAL_N]:
        L.append(f"- **{sc}*** {d} - dist {dist:.2f}{' - ' + str(u) + ' helpful' if u else ''}: {t}")
    L += ["", f"## {C.CARD_ENDORSED_N} most helpful", ""]
    for sc, d, u, src, dist, t in sorted(rows, key=lambda r: -r[2])[:C.CARD_ENDORSED_N]:
        L.append(f"- **{sc}*** {d} - {u} helpful: {t}")
    out = args.out or os.path.join(C.LABELS_DIR, f"card_cluster{args.cluster}.md")
    open(out, "w", encoding="utf-8").write("\n".join(L) + "\n")
    print(f"wrote {out}")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def scoped(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--run", type=int)
        g.add_argument("--all", action="store_true")

    p = sub.add_parser("export"); scoped(p); p.add_argument("--out")
    for name in ("weakest", "ambiguous"):
        p = sub.add_parser(name); scoped(p)
        p.add_argument("-n", type=int, default=C.REVIEW_DEFAULT_N)
        p.add_argument("--include-unassigned", action="store_true")
    p = sub.add_parser("flag"); p.add_argument("log_id", type=int)
    p.add_argument("--correct", help="cluster id it should have gone to, or 'bucket'")
    p.add_argument("--note")
    p = sub.add_parser("unflag"); p.add_argument("log_id", type=int)
    p = sub.add_parser("flags"); p.add_argument("--out")
    p = sub.add_parser("labelset"); scoped(p)
    p.add_argument("--weakest", type=int, default=C.LABELSET_WEAKEST_N)
    p.add_argument("--ambiguous", type=int, default=C.LABELSET_AMBIGUOUS_N)
    p.add_argument("--seed", type=int, default=C.LABELSET_SEED)
    p.add_argument("--log-ids", help="comma-separated log ids to export instead of picking")
    p.add_argument("--out")
    p = sub.add_parser("purityset"); p.add_argument("--cluster", type=int, required=True, help="display id")
    p.add_argument("-n", type=int, default=C.PURITY_SAMPLE_N); p.add_argument("--seed", type=int, default=C.PURITY_SEED)
    p.add_argument("--question", required=True); p.add_argument("--out")
    p = sub.add_parser("card"); p.add_argument("--cluster", type=int, required=True, help="display id")
    p.add_argument("--out")
    args = ap.parse_args()

    con = sqlite3.connect(C.DB_PATH)
    S.ensure_schema(con)
    if args.cmd == "export":
        cmd_export(con, args)
    elif args.cmd == "weakest":
        cmd_ranked(con, args, "COALESCE(l.distance, l.nearest_distance) DESC",
                   f"{args.n} weakest fits (largest distance to centroid)")
    elif args.cmd == "ambiguous":
        cmd_ranked(con, args, "l.gap ASC",
                   f"{args.n} most ambiguous (smallest gap between nearest and 2nd nearest)")
    elif args.cmd == "flag":
        cmd_flag(con, args)
    elif args.cmd == "unflag":
        cmd_unflag(con, args)
    elif args.cmd == "flags":
        cmd_flags(con, args)
    elif args.cmd == "labelset":
        cmd_labelset(con, args)
    elif args.cmd == "purityset":
        cmd_purityset(con, args)
    elif args.cmd == "card":
        cmd_card(con, args)


if __name__ == "__main__":
    main()
