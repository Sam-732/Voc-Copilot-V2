"""
Append hand verdicts on borderline assignments to labels/threshold_labels.csv.

One at a time:
    python labels/record_label.py LOG_ID right|wrong "reasoning" ["failure type"]
A filled-in batch from `assignlog.py labelset` (rows with an empty verdict are skipped):
    python labels/record_label.py --from-csv labels/batch_XXXX.csv

The writing itself is labelstore.record(), shared with the web app's labelling page.
cluster / second_cluster are internal ids (as in reviews.db); *_display_id are the ids from
themes_v2.json that the dashboard shows. The CSV keeps the numbers as they were when the verdict
was given (metric, threshold, distances), so the record stays meaningful after the threshold or
metric changes. A log id that's already recorded is skipped, not duplicated.
"""
import csv, json, os, sqlite3, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
os.chdir(os.path.dirname(HERE))
import config as C
import labelstore


def session_pool(log_id):
    path = os.path.join(HERE, "_session.json")
    if not os.path.exists(path):
        return ""
    return dict(map(tuple, json.load(open(path))["picks"])).get(log_id, "")


def record(con, log_id, verdict, reasoning, failure_type="", pool=None):
    try:
        ok = labelstore.record(con, log_id, verdict, reasoning, failure_type,
                               pool if pool is not None else session_pool(log_id))
    except labelstore.LabelError as e:
        sys.exit(str(e))
    print(f"recorded log {log_id}: {verdict.strip().lower()}" if ok else f"log {log_id} already recorded - skipped")
    return ok


def main():
    con = sqlite3.connect(C.DB_PATH)
    if sys.argv[1] == "--from-csv":
        with open(sys.argv[2], encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        done = [r for r in rows if r.get("verdict", "").strip()]
        n = sum(record(con, int(r["log_id"]), r["verdict"], r.get("reason", ""),
                       r.get("failure_type", ""), r.get("pool", "")) for r in done)
        print(f"{n} recorded, {len(rows) - len(done)} rows without a verdict left out")
        return
    record(con, int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4] if len(sys.argv) > 4 else "")


if __name__ == "__main__":
    main()
