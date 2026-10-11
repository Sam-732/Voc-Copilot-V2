"""
Writes made by a person, not by the pipeline. Shared by assignlog.py (command line) and app.py
(web app), so a flag is written exactly one way.

A flag marks one assignment decision - one assignment_log row - as wrong. v1's own 4,126 reviews
were placed by v1's clustering, not by a daily decision, so they have no log row and can't be flagged.
"""
from datetime import datetime


class FlagError(ValueError):
    pass


def flag_assignment(con, log_id, correct=None, note=None):
    """correct: internal cluster id it should have gone to, 'bucket', or None if unknown.
    Re-flagging the same decision replaces the earlier flag. Commits."""
    row = con.execute("SELECT review_id, outcome, cluster FROM assignment_log WHERE log_id = ?",
                      (log_id,)).fetchone()
    if not row:
        raise FlagError(f"no assignment decision with log id {log_id}")
    if correct not in (None, "bucket"):
        if not con.execute("SELECT 1 FROM centroids WHERE cluster = ?", (int(correct),)).fetchone():
            raise FlagError(f"cluster {correct} does not exist")
        correct = str(int(correct))
    if correct == "bucket" and row[1] == "unassigned":
        raise FlagError("this review is already in the bucket")
    with con:
        con.execute("INSERT OR REPLACE INTO assignment_flags VALUES (?,?,?,?,?,?)",
                    (log_id, row[0], datetime.now().isoformat(), "wrong", correct, note or None))
    return {"review_id": row[0], "outcome": row[1], "cluster": row[2]}


def unflag_assignment(con, log_id):
    """Removes the flag on one decision. Returns True if there was one. Commits."""
    with con:
        return con.execute("DELETE FROM assignment_flags WHERE log_id = ?", (log_id,)).rowcount > 0
