"""
Builds dashboard_v2.html from reviews.db (v2, phase 4). Same stack as v1's dashboard.py:
one self-contained HTML file, no internet, no libraries. v1's dashboard.py is untouched.

Grouping comes from themes_v2.json (via taxonomy.py); ranks from rank.py's theme snapshots.
Run after assign.py and rank.py:
    python dashboard_v2.py

Sections:
  - complaint themes, ranked by total review count, with rank change since the previous run,
    recency, volume over time, sub-themes (clusters) and every source review on expand.
    A sub-theme whose share of the theme's helpful votes is >= ENDORSEMENT_LIFT x its share of
    reviews (and has >= ENDORSEMENT_MIN_VOTES) is flagged "most endorsed". Display only.
  - shown separately: general anger, language clusters
  - Positive: one line with its count
  - clusters found in the unassigned bucket, awaiting a decision
  - the unassigned bucket, with its count and reviews
v1 rule kept: anything under WEAK_SIGNAL_MIN reviews shows counts only, never percentages,
and is labelled weak signal. Citations use reviews_raw text, verbatim and unedited.
Cluster numbers shown are display ids from themes_v2.json, not the internal ids in reviews.db.
"""
import json, sqlite3, collections
from datetime import date, timedelta

import config as C
import taxonomy


def bins_for(first, last):
    """VOLUME_BIN_DAYS-day bins anchored on the newest day, so the latest bar is a full period.
    Returns [(start, end, days)] oldest first; the oldest may be partial."""
    out, end = [], last
    while end >= first:
        start = max(end - timedelta(days=C.VOLUME_BIN_DAYS - 1), first)
        out.append((start, end, (end - start).days + 1))
        end = start - timedelta(days=1)
    return out[::-1]


def build_payload(con, with_ids=False):
    """Everything the page shows, read from reviews.db. Only SELECTs.
    Used by main() for the static file and by app.py for the live page.
    with_ids: include each review's id, so the app can link to its detail page. The static
    file leaves them out, so its data stays as before.
    Returns (payload, previous run id)."""
    run_id = con.execute("SELECT MAX(run_id) FROM theme_rank_snapshots").fetchone()[0]
    if run_id is None:
        raise SystemExit("no theme snapshot - run rank.py first")
    prev_run = con.execute("SELECT MAX(run_id) FROM theme_rank_snapshots WHERE run_id < ?", (run_id,)).fetchone()[0]
    snap = {k: (kind, n, r, p, m) for k, kind, n, r, p, m in con.execute(
        "SELECT theme, kind, total, rank, prev_rank, movement FROM theme_rank_snapshots WHERE run_id = ?", (run_id,))}
    run_info = con.execute("SELECT started_at, threshold FROM assign_runs WHERE run_id = ?", (run_id,)).fetchone()

    rows = con.execute("""
        SELECT a.status, a.cluster, a.source, r.review_id, r.content, r.score, r.at, r.thumbs_up
        FROM assignments a JOIN reviews_raw r USING (review_id)
        WHERE a.status IN ('clustered', 'unassigned')""").fetchall()
    themes, clusters = taxonomy.load({c for s, c, *_ in rows if s == "clustered"})

    all_dates = [date.fromisoformat(r[6][:10]) for r in rows]
    first, last = min(all_dates), max(all_dates)
    bins = bins_for(first, last)
    v2_start = con.execute("SELECT MIN(c.at) FROM assignments a JOIN reviews_clean c USING (review_id) "
                           "WHERE a.source != 'initial'").fetchone()[0]
    recent_from = last - timedelta(days=C.RECENT_DAYS - 1)

    def bin_index(d):
        for i, (s, e, _) in enumerate(bins):
            if s <= d <= e:
                return i

    by_cluster = collections.defaultdict(list)
    for status, cid, source, rid, text, score, at, thumbs in rows:
        key = cid if status == "clustered" else "bucket"
        by_cluster[key].append({**({"id": rid} if with_ids else {}),
                                "t": text or "", "s": score, "d": at[:10], "u": thumbs or 0,
                                "n": source != "initial",
                                "c": clusters[cid]["id"] if status == "clustered" else None})

    def summarise(revs):
        vol = [0] * len(bins)
        for r in revs:
            vol[bin_index(date.fromisoformat(r["d"]))] += 1
        revs = sorted(revs, key=lambda r: r["d"], reverse=True)   # newest first...
        revs.sort(key=lambda r: -r["u"])                           # ...within most helpful first
        n = len(revs)
        return {"total": n, "new": sum(r["n"] for r in revs),
                "recent": sum(date.fromisoformat(r["d"]) >= recent_from for r in revs),
                "latest": max((r["d"] for r in revs), default=""),
                "mean": round(sum(r["s"] for r in revs) / n, 1) if n else None,
                "votes": sum(r["u"] for r in revs), "vol": vol, "reviews": revs,
                "weak": n < C.WEAK_SIGNAL_MIN}

    internal_of = {v["id"]: c for c, v in clusters.items()}
    out = []
    for key, t in themes.items():
        kind, total, rank, prev, move = snap.get(key, (t["kind"], 0, None, None, None))
        members = [internal_of[i] for i in t["clusters"]]
        revs = [r for c in members for r in by_cluster.get(c, [])]
        s = summarise(revs)
        subs = []
        for c in members:
            cr = by_cluster.get(c, [])
            v = sum(r["u"] for r in cr)
            subs.append({"id": clusters[c]["id"], "name": clusters[c]["name"], "total": len(cr), "votes": v,
                         "top": max((r["u"] for r in cr), default=0),
                         "revShare": len(cr) / s["total"] if s["total"] else 0,
                         "voteShare": v / s["votes"] if s["votes"] else 0,
                         "weak": len(cr) < C.WEAK_SIGNAL_MIN})
        for x in subs:
            x["lift"] = round(x["voteShare"] / x["revShare"], 1) if x["revShare"] else 0
            x["endorsed"] = (len(subs) > 1 and x["votes"] >= C.ENDORSEMENT_MIN_VOTES
                             and x["lift"] >= C.ENDORSEMENT_LIFT)
        subs.sort(key=lambda x: (not x["endorsed"], -x["total"]))
        endorsed = next((x for x in subs if x["endorsed"]), None)
        top_review = None
        if endorsed:
            top_review = next(r for r in s["reviews"] if r["c"] == endorsed["id"])
        out.append({"key": key, "name": t["name"], "kind": t["kind"], "rank": rank, "prev": prev,
                    "move": move, "subs": subs, "endorsed": endorsed, "endorsedTop": top_review,
                    "decidedBy": t.get("decided_by", ""), **s})

    complaint_total = sum(x["total"] for x in out if x["kind"] == "complaint")
    for x in out:
        # v1 rule: no percentages on weak signals
        x["share"] = (round(x["total"] / complaint_total * 100)
                      if x["kind"] == "complaint" and not x["weak"] else None)
    comp = sorted([x for x in out if x["kind"] == "complaint"], key=lambda x: (x["rank"], -x["total"]))

    found = []   # each bucket-found cluster on its own row
    for i in themes[taxonomy.UNMAPPED_THEME]["clusters"]:
        c = internal_of[i]
        found.append({"id": i, "name": clusters[c]["name"], **summarise(by_cluster.get(c, []))})
    found.sort(key=lambda x: -x["total"])

    bucket = summarise(by_cluster.get("bucket", []))
    bucket_hist = [n for (n,) in con.execute(
        "SELECT total FROM rank_snapshots WHERE grp = 'bucket' AND run_id >= "
        "(SELECT MAX(run_id) FROM assign_runs WHERE status='reset') ORDER BY run_id")]
    prev_bucket = con.execute("SELECT total FROM rank_snapshots WHERE grp='bucket' AND run_id=?",
                              (prev_run,)).fetchone()

    payload = {
        "funnel": {"raw": con.execute("SELECT COUNT(*) FROM reviews_raw").fetchone()[0],
                   "clean": con.execute("SELECT COUNT(*) FROM reviews_clean").fetchone()[0],
                   "complaint": complaint_total, "bucket": bucket["total"]},
        "run": {"id": run_id, "at": run_info[0][:16].replace("T", " "), "threshold": run_info[1],
                "prevBucket": prev_bucket[0] if prev_bucket else None},
        "window": {"from": first.isoformat(), "to": last.isoformat(), "v2start": v2_start[:10],
                   "recentDays": C.RECENT_DAYS},
        "bins": [{"from": s.isoformat(), "to": e.isoformat(), "days": d} for s, e, d in bins],
        "binDays": C.VOLUME_BIN_DAYS, "weakMin": C.WEAK_SIGNAL_MIN, "page": C.DASHBOARD_REVIEWS_PAGE,
        "endorse": {"lift": C.ENDORSEMENT_LIFT, "minVotes": C.ENDORSEMENT_MIN_VOTES},
        "complaint": comp,
        "separate": [x for x in out if x["kind"] == "separate"],
        "positive": next(({k: v for k, v in x.items() if k != "reviews"} for x in out if x["kind"] == "positive"), None),
        "found": found,
        "bucket": {**bucket, "hist": bucket_hist},
    }
    return payload, prev_run


def render_html(payload, nav=""):
    """nav: extra HTML placed above the header (the app's navigation bar); empty for the static file."""
    return (TEMPLATE.replace("__STYLE__", STYLE).replace("__NAV__", nav)
            .replace("__DATA__", json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")))


def main():
    con = sqlite3.connect(C.DB_PATH)
    payload, prev_run = build_payload(con)
    with open(C.DASHBOARD_V2_FILE, "w", encoding="utf-8") as f:
        f.write(render_html(payload))
    comp = payload["complaint"]
    print(f"wrote {C.DASHBOARD_V2_FILE} - assign run {payload['run']['id']} vs previous {prev_run}")
    print(f"  {len(comp)} complaint themes, {len(payload['separate'])} shown separately, "
          f"positive {payload['positive']['total'] if payload['positive'] else 0}, "
          f"{len(payload['found'])} bucket-found clusters, bucket {payload['bucket']['total']}")
    print(f"  endorsed sub-themes: {[(x['name'], x['endorsed']['id']) for x in comp if x['endorsed']]}")


STYLE = r""":root{
  --bg:#f4f6f5; --surface:#fff; --ink:#131a19; --muted:#5c6a66; --faint:#8d9b97;
  --rule:#e0e7e5; --rule-soft:#edf2f1; --accent:#0e6a66; --accent-soft:#cfe5e3;
  --warn:#8a5a14; --flag:#9c3a20; --up:#0e6a66; --down:#9c3a20;
  --mono:"IBM Plex Mono",ui-monospace,Consolas,monospace;
  --sans:system-ui,-apple-system,"Segoe UI",sans-serif;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0d1312; --surface:#141c1b; --ink:#e6ecea; --muted:#9aa8a4; --faint:#6e7b78;
  --rule:#26312f; --rule-soft:#1c2524; --accent:#5cc0b7; --accent-soft:#1b3835;
  --warn:#d6b25e; --flag:#e08f74; --up:#5cc0b7; --down:#e08f74;
}}
:root[data-theme=dark]{
  --bg:#0d1312; --surface:#141c1b; --ink:#e6ecea; --muted:#9aa8a4; --faint:#6e7b78;
  --rule:#26312f; --rule-soft:#1c2524; --accent:#5cc0b7; --accent-soft:#1b3835;
  --warn:#d6b25e; --flag:#e08f74; --up:#5cc0b7; --down:#e08f74;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--sans);
     font-size:15px;line-height:1.55;-webkit-font-smoothing:antialiased}
.wrap{max-width:1000px;margin:0 auto;padding-inline:20px;padding-block:40px 80px}
header{border-bottom:2px solid var(--ink);padding-bottom:22px;margin-bottom:28px}
h1{margin:0 0 6px;font-size:clamp(1.7rem,4vw,2.3rem);letter-spacing:-.02em}
.scope{color:var(--muted);font-size:14px;margin:0;max-width:70ch}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:26px 0}
.tile{background:var(--surface);border:1px solid var(--rule);border-radius:3px;padding:14px 16px}
.tile .v{font-size:1.75rem;font-weight:600;font-variant-numeric:tabular-nums;letter-spacing:-.02em}
.tile .k{font-family:var(--mono);font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);margin-top:2px}
.tile .n{font-size:12px;color:var(--muted);margin-top:6px;line-height:1.35}
.caveat{border-left:3px solid var(--warn);background:var(--surface);padding:12px 16px;
        font-size:13.5px;color:var(--muted);margin-bottom:26px}
.caveat b{color:var(--ink);font-weight:600}
.caveat ul{margin:6px 0 0;padding-left:18px}
h2{font-size:12px;font-family:var(--mono);letter-spacing:.1em;text-transform:uppercase;
   color:var(--faint);margin:38px 0 6px;font-weight:500}
.h2note{color:var(--muted);font-size:13px;margin:0 0 14px;max-width:75ch}
.colhead{display:grid;grid-template-columns:34px 1fr 88px 110px 70px;gap:14px;padding:0 18px 6px;
  font-family:var(--mono);font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint)}
.colhead span:nth-child(n+3){text-align:right}
.cl{background:var(--surface);border:1px solid var(--rule);border-radius:3px;margin-bottom:10px}
.thead{display:grid;grid-template-columns:34px 1fr 88px 110px 70px;gap:14px;align-items:center;
       padding:13px 18px;cursor:pointer}
.thead:hover{background:var(--rule-soft)}
.thead:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
.static .thead{cursor:default} .static .thead:hover{background:none}
.rk{font-family:var(--mono);font-size:13px;color:var(--ink);font-weight:600}
.tname{font-size:15.5px;font-weight:600;margin:0 0 3px;line-height:1.3}
.tname .kind{font-weight:400;font-size:11px;font-family:var(--mono);color:var(--faint);margin-left:6px}
.tmeta{font-family:var(--mono);font-size:11.5px;color:var(--muted);display:flex;flex-wrap:wrap;gap:3px 14px}
.num{font-variant-numeric:tabular-nums;text-align:right;font-family:var(--mono);font-size:13px}
.num b{font-size:16px;font-family:var(--sans)}
.num small{display:block;color:var(--faint);font-size:10.5px}
.mv{font-family:var(--mono);font-size:12px;text-align:right;white-space:nowrap}
.mv small{display:block;color:var(--faint);font-size:10.5px}
.mv.up{color:var(--up)} .mv.down{color:var(--down)} .mv.same{color:var(--faint)}
.mv.new{color:var(--warn);font-weight:600}
.weak{display:inline-block;font-family:var(--mono);font-size:10px;letter-spacing:.06em;text-transform:uppercase;
      border:1px solid var(--warn);color:var(--warn);border-radius:2px;padding:0 5px;margin-left:6px;vertical-align:1px}
.spark{display:block;width:88px;height:24px;margin-left:auto}
.endorse{margin-top:8px;border-left:3px solid var(--accent);padding:4px 0 4px 10px;font-size:13px;color:var(--muted)}
.endorse b{color:var(--ink)}
.endorse .lbl{font-family:var(--mono);font-size:10.5px;letter-spacing:.08em;text-transform:uppercase;color:var(--accent)}
.endorse q{display:block;margin-top:2px;color:var(--ink);font-style:italic}
.body{display:none;border-top:1px solid var(--rule);padding:12px 18px 16px}
.cl.open .body{display:block}
.subs{width:100%;border-collapse:collapse;font-size:13px;margin:4px 0 16px}
.subs th{font-family:var(--mono);font-size:10px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);
         text-align:left;padding:5px 8px;border-bottom:1px solid var(--rule);font-weight:500}
.subs td{padding:6px 8px;border-bottom:1px solid var(--rule-soft)}
.subs td.n{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums}
.subs th.n{text-align:right}
.subs tr.endorsed td{background:var(--accent-soft)}
.chart{position:relative;margin:4px 0 14px}
.chart svg{display:block;width:100%;height:auto;overflow:visible}
.chart .cap{font-family:var(--mono);font-size:11px;color:var(--faint);margin-bottom:4px}
.tip{position:absolute;pointer-events:none;background:var(--ink);color:var(--bg);font-family:var(--mono);
     font-size:11px;padding:4px 7px;border-radius:3px;white-space:nowrap;transform:translate(-50%,-100%);display:none}
.rev{border-left:3px solid var(--rule);padding:7px 0 7px 14px;margin:12px 0;font-size:13.5px}
.rev.up{border-left-color:var(--accent)}
.rmeta{font-family:var(--mono);font-size:11px;color:var(--faint);margin-bottom:3px;display:flex;flex-wrap:wrap;gap:3px 12px}
.stars{color:var(--flag);font-weight:600;letter-spacing:1px}
.stars .nostar{color:var(--rule)}
.votes{color:var(--accent);font-weight:500}
.newtag{color:var(--warn)}
.more{font:inherit;font-family:var(--mono);font-size:12px;margin-top:8px;padding:6px 12px;cursor:pointer;
      background:var(--surface);color:var(--accent);border:1px solid var(--rule);border-radius:3px}
.more:focus-visible{outline:2px solid var(--accent)}
.dashed{border:1px dashed var(--warn)}
footer{margin-top:44px;padding-top:20px;border-top:1px solid var(--rule);font-family:var(--mono);
       font-size:11.5px;color:var(--faint);line-height:1.7;max-width:80ch}
@media (max-width:620px){
  .thead,.colhead{grid-template-columns:28px 1fr 70px;gap:8px}
  .colhead span:nth-child(4),.colhead span:nth-child(5),.thead .c4,.thead .c5{display:none}
  .wrap{padding-block:28px 60px}
}
"""
# ^ shared with the app's other pages (templates/base.html) so every page looks the same.

TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Swiggy VoC — daily</title>
<style>
__STYLE__</style></head><body>
<div class="wrap">
__NAV__<header>
  <h1>Swiggy — Voice of Customer, daily</h1>
  <p class="scope" id="scope"></p>
</header>
<div class="tiles" id="tiles"></div>
<div class="caveat" id="caveat"></div>

<h2>Complaint themes</h2>
<p class="h2note">Ranked by total review count. Recency is shown alongside and is not part of the rank.
Open a theme for its sub-themes, weekly volume and every source review.</p>
<div class="colhead"><span>#</span><span>Theme</span><span>Reviews</span><span>Change</span><span>Volume</span></div>
<div id="complaint"></div>

<h2>Shown separately</h2>
<p class="h2note">Real reviews that don't name a specific, fixable complaint, so they're kept out of the ranking.</p>
<div id="separate"></div>

<h2>Positive</h2>
<div id="positive"></div>

<h2>Found in the unassigned bucket — awaiting review</h2>
<p class="h2note">Clusters the tool found on its own by re-clustering reviews that fit no existing theme.
Not yet named or placed; they join a theme once reviewed.</p>
<div id="found"></div>

<h2>Unassigned bucket</h2>
<p class="h2note">New reviews too far from every cluster to be placed. When it holds more than 40, it is
re-clustered on its own; anything found appears in the section above.</p>
<div id="bucket"></div>

<footer id="foot"></footer>
</div>
<div class="tip" id="tip" role="tooltip"></div>
<script id="payload" type="application/json">__DATA__</script>
<script>
const D = JSON.parse(document.getElementById('payload').textContent);
const esc = s => String(s).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt = n => n.toLocaleString('en-IN');
const pct = x => Math.round(x * 100) + '%';
const W = D.window;

document.getElementById('scope').innerHTML =
  `Google Play reviews, India, ${W.from} → ${W.to}. v1 clustered the reviews up to ${W.v2start}; every review since
   is placed daily into the nearest existing cluster (euclidean distance under ${D.run.threshold}) or left in the
   unassigned bucket. Last run ${esc(D.run.at)}. Every review shown is verbatim.`;

const bDelta = D.run.prevBucket == null ? null : D.bucket.total - D.run.prevBucket;
document.getElementById('tiles').innerHTML = [
  [D.funnel.raw,'Reviews collected','stored untouched'],
  [D.funnel.clean,'With real content','5 words or more, duplicates collapsed'],
  [D.funnel.complaint,'In a complaint theme',`across ${D.complaint.length} themes`],
  [D.funnel.bucket,'Unassigned bucket','new reviews that fit no cluster' + (bDelta == null ? '' : ` (${bDelta >= 0 ? '+' : ''}${bDelta} since previous run)`)],
].map(([v,k,n]) => `<div class="tile"><div class="v">${fmt(v)}</div><div class="k">${k}</div><div class="n">${n}</div></div>`).join('');

document.getElementById('caveat').innerHTML = `<b>How far to trust this.</b>
<ul>
<li>Reviewers are a self-selected, mostly angry minority. Theme size is not incidence.</li>
<li>v1's clustering agreed with a hand-labelled set only 21% of the time. Daily placement adds its own error.
The ${D.run.threshold} cut-off was set from just 10 hand-checked borderline placements; 6 of them were wrong,
mostly reviews whose real complaint has no cluster at all.</li>
<li>Each review counts once, under its nearest cluster; a review with two complaints can land under the secondary one.</li>
<li>Roughly 2% of placed complaints land in Positive and never reach the complaint ranking.</li>
<li>Bars before ${W.v2start} are v1 cluster membership; after it, daily placement. The two methods differ, so a step at that date is not a trend.</li>
</ul>`;

function moveCell(t){
  if (t.move === 'new') return `<span class="mv new" title="not in the previous run">NEW</span>`;
  if (t.move === 'up') return `<span class="mv up">▲ ${t.prev - t.rank}<small>from #${t.prev}</small></span>`;
  if (t.move === 'down') return `<span class="mv down">▼ ${t.rank - t.prev}<small>from #${t.prev}</small></span>`;
  if (t.move === 'same') return `<span class="mv same">— same</span>`;
  return '';
}

function spark(vol){
  const max = Math.max(1, ...vol), w = 88, h = 24, bw = w / vol.length;
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" aria-hidden="true">${vol.map((v,i) => {
    const bh = Math.max(v ? 1.5 : 0, v / max * (h - 2));
    return `<rect x="${i*bw + 1}" y="${h - bh}" width="${bw - 2}" height="${bh}" rx="1" fill="var(--accent)" opacity="${D.bins[i].days < D.binDays ? .45 : 1}"/>`;
  }).join('')}</svg>`;
}

function chart(vol, label){
  const W2 = 600, H = 120, padL = 30, padB = 20, padT = 10, n = vol.length;
  const max = Math.max(1, ...vol), bw = (W2 - padL) / n;
  const y = v => padT + (H - padT - padB) * (1 - v / max);
  // marker at the day the switch happens, inside its bar
  const v2i = D.bins.findIndex(b => b.to >= W.v2start);
  const b0 = v2i < 0 ? null : D.bins[v2i];
  const v2x = v2i < 0 ? null : padL + v2i * bw + bw * ((Date.parse(W.v2start) - Date.parse(b0.from)) / 864e5) / b0.days;
  const bars = vol.map((v,i) => {
    const b = D.bins[i], x = padL + i*bw + 1, top = y(v), part = b.days < D.binDays;
    const h = Math.max(0, H - padB - top);
    const tipTxt = `${b.from} → ${b.to}${part ? ` (${b.days} days only)` : ''}: ${v} review${v===1?'':'s'}`;
    return `<g class="hit" data-tip="${esc(tipTxt)}"><rect x="${padL + i*bw}" y="${padT}" width="${bw}" height="${H-padT-padB}" fill="transparent"/>
      <path d="M${x},${H-padB} V${top + Math.min(4,h)} Q${x},${top} ${x+4},${top} H${x+bw-6} Q${x+bw-2},${top} ${x+bw-2},${top+Math.min(4,h)} V${H-padB} Z"
        fill="var(--accent)" opacity="${part ? .45 : 1}"/></g>`;
  }).join('');
  const labels = D.bins.map((b,i) => (i % 2 === (n-1) % 2) ?
    `<text x="${padL + i*bw + bw/2}" y="${H-5}" text-anchor="middle" font-size="10" fill="var(--faint)" font-family="var(--mono)">${b.to.slice(5)}</text>` : '').join('');
  return `<div class="chart"><div class="cap">${esc(label)} — reviews per ${D.binDays} days, by review date (bar = period ending on the date shown)</div>
  <svg viewBox="0 0 ${W2} ${H}" role="img" aria-label="${esc(label)}: ${vol.join(', ')} reviews per period">
    <line x1="${padL}" x2="${W2}" y1="${H-padB}" y2="${H-padB}" stroke="var(--rule)"/>
    <text x="${padL-6}" y="${padT+4}" text-anchor="end" font-size="10" fill="var(--faint)" font-family="var(--mono)">${max}</text>
    <text x="${padL-6}" y="${H-padB}" text-anchor="end" font-size="10" fill="var(--faint)" font-family="var(--mono)">0</text>
    ${bars}
    ${v2x == null ? '' : `<line x1="${v2x}" x2="${v2x}" y1="${padT-4}" y2="${H-padB}" stroke="var(--warn)" stroke-dasharray="3 3"/>
    <text x="${v2x+4}" y="${padT+2}" font-size="10" fill="var(--warn)" font-family="var(--mono)">daily placement from ${W.v2start}</text>`}
    ${labels}
  </svg></div>`;
}

function reviewsHtml(revs, from, to){
  return revs.slice(from, to).map(r => `<div class="rev${r.u >= 5 ? ' up' : ''}">
    <div class="rmeta"><span class="stars" aria-label="${r.s} stars">${'★'.repeat(r.s)}<span class="nostar">${'★'.repeat(5-r.s)}</span> ${r.s}★</span>
    <span>${r.d}</span>${r.u ? `<span class="votes">${fmt(r.u)} found helpful</span>` : ''}
    ${r.c != null ? `<span>sub-theme ${r.c}</span>` : ''}
    ${r.n ? '<span class="newtag">placed daily</span>' : ''}
    ${r.id ? `<a href="/review/${encodeURIComponent(r.id)}" style="color:var(--accent)">${r.n ? 'details · flag' : 'details'}</a>` : ''}</div>${esc(r.t)}</div>`).join('');
}

function subsTable(t){
  if (!t.subs || t.subs.length < 2) return '';
  return `<table class="subs"><thead><tr><th>Sub-theme</th><th class="n">Reviews</th><th class="n">Helpful votes</th><th class="n">Top review</th></tr></thead><tbody>
  ${t.subs.map(s => `<tr class="${s.endorsed ? 'endorsed' : ''}"><td>${s.endorsed ? '★ ' : ''}${s.id} · ${esc(s.name)}${s.weak ? '<span class="weak">weak signal</span>' : ''}</td>
    <td class="n">${fmt(s.total)}${s.weak ? '' : ` <span style="color:var(--faint)">(${pct(s.revShare)})</span>`}</td>
    <td class="n">${fmt(s.votes)}${s.weak || !t.votes ? '' : ` <span style="color:var(--faint)">(${pct(s.voteShare)})</span>`}</td>
    <td class="n">${fmt(s.top)}</td></tr>`).join('')}</tbody></table>`;
}

function attachBody(sec, t, label){
  const body = sec.querySelector('.body');
  let shown = 0;
  const more = () => {
    body.querySelector('.revs').insertAdjacentHTML('beforeend', reviewsHtml(t.reviews, shown, shown + D.page));
    shown = Math.min(t.reviews.length, shown + D.page);
    const btn = body.querySelector('.more');
    if (shown >= t.reviews.length) btn.remove();
    else btn.textContent = `Show ${Math.min(D.page, t.reviews.length - shown)} more of ${fmt(t.reviews.length - shown)} remaining`;
  };
  const open = () => {
    if (!body.dataset.built){
      body.innerHTML = subsTable(t) + chart(t.vol, label) +
        `<div class="rmeta" style="margin-bottom:0">Source reviews, most helpful first</div><div class="revs"></div><button class="more"></button>`;
      body.querySelector('.more').addEventListener('click', more);
      body.dataset.built = 1; more(); hookTips(body);
    }
  };
  const head = sec.querySelector('.thead');
  const toggle = () => { open(); sec.classList.toggle('open'); head.setAttribute('aria-expanded', sec.classList.contains('open')); };
  head.addEventListener('click', toggle);
  head.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' '){ e.preventDefault(); toggle(); }});
}

function endorseHtml(t){
  const e = t.endorsed, r = t.endorsedTop;
  if (!e) return '';
  const snippet = r.t.length > 140 ? r.t.slice(0, 140) + '…' : r.t;
  return `<div class="endorse"><span class="lbl">★ Most endorsed inside this theme</span><br>
    <b>${esc(e.name)}</b> — ${fmt(e.total)} reviews (${pct(e.revShare)} of theme) · <b>${pct(e.voteShare)}</b> of the theme's helpful votes
    <q>${esc(snippet)}</q><span style="font-family:var(--mono);font-size:11px">${fmt(r.u)} found helpful · ${r.d}</span></div>`;
}

function rowHtml(t, opts){
  const size = t.weak ? `<b>${fmt(t.total)}</b><small>weak signal</small>` :
               `<b>${fmt(t.total)}</b><small>${t.share != null ? t.share + '% of complaints' : '&nbsp;'}</small>`;
  const subIds = t.subs ? `sub-themes ${t.subs.map(s => s.id).join(', ')}` : `cluster ${t.id}`;
  return `<section class="cl${opts.cls || ''}"><div class="thead" tabindex="0" role="button" aria-expanded="false">
    <span class="rk">${opts.rank || '—'}</span>
    <div><h3 class="tname">${esc(t.name)}${opts.tag || ''}${t.weak ? '<span class="weak">weak signal</span>' : ''}</h3>
      <div class="tmeta"><span>${subIds}</span><span>+${fmt(t.new)} placed daily</span>
      <span>last ${W.recentDays} days: ${fmt(t.recent)}</span><span>latest ${t.latest}</span>
      ${t.mean != null ? `<span>mean ${t.mean.toFixed(1)}★</span>` : ''}${t.votes ? `<span>${fmt(t.votes)} helpful votes</span>` : ''}</div>
      ${opts.endorse ? endorseHtml(t) : ''}</div>
    <span class="num c3">${size}</span>
    <span class="c4">${opts.move || ''}</span>
    <span class="c5">${spark(t.vol)}</span>
  </div><div class="body"></div></section>`;
}

function render(el, list, opts){
  el.innerHTML = list.map(t => rowHtml(t, opts(t))).join('');
  [...el.children].forEach((sec, i) => attachBody(sec, list[i], list[i].name));
}
render(document.getElementById('complaint'), D.complaint, t => ({rank: '#' + t.rank, move: moveCell(t), endorse: true}));
render(document.getElementById('separate'), D.separate, t => ({}));
render(document.getElementById('found'), D.found, t => ({cls: ' dashed', tag: '<span class="kind">awaiting review</span>'}));

const P = D.positive;
document.getElementById('positive').innerHTML = !P ? '' : `<section class="cl static"><div class="thead">
  <span class="rk">—</span>
  <div><h3 class="tname">Positive <span class="kind">excluded from the complaint ranking</span></h3>
  <div class="tmeta"><span>${P.subs.length} clusters of praise</span><span>+${fmt(P.new)} placed daily</span>
  <span>last ${W.recentDays} days: ${fmt(P.recent)}</span><span>mean ${P.mean.toFixed(1)}★</span></div></div>
  <span class="num c3"><b>${fmt(P.total)}</b><small>reviews</small></span><span class="c4"></span><span class="c5">${spark(P.vol)}</span>
</div></section>`;

const B = D.bucket, bsec = document.getElementById('bucket');
bsec.innerHTML = `<section class="cl dashed"><div class="thead" tabindex="0" role="button" aria-expanded="false">
  <span class="rk">—</span>
  <div><h3 class="tname">Unassigned</h3><div class="tmeta"><span>last ${W.recentDays} days: ${fmt(B.recent)}</span>
  <span>latest ${B.latest || '—'}</span>${B.mean != null ? `<span>mean ${B.mean.toFixed(1)}★</span>` : ''}
  <span>after each run: ${B.hist.join(' → ')}</span></div></div>
  <span class="num c3"><b>${fmt(B.total)}</b><small>current count</small></span>
  <span class="c4 mv same">${bDelta == null ? '' : (bDelta >= 0 ? '+' : '') + bDelta + '<small>since previous</small>'}</span>
  <span class="c5">${spark(B.vol)}</span>
</div><div class="body"></div></section>`;
attachBody(bsec.firstElementChild, B, 'Unassigned bucket');

document.getElementById('foot').textContent =
  `Built from reviews.db by dashboard_v2.py, themes from themes_v2.json. Assign run ${D.run.id}. Ranking: total reviews
   per theme, descending; tied totals share a rank; no severity weighting. "Most endorsed" flags a sub-theme holding at
   least ${D.endorse.lift}x its share of the theme's helpful votes (and ${D.endorse.minVotes}+ votes) — display only, never
   part of the rank. Anything under ${D.weakMin} reviews shows counts only and is labelled weak signal. Sub-theme numbers
   are display ids. Play Store has no per-review URL, so citation means the verbatim text, rating and date.`;

const tip = document.getElementById('tip');
function hookTips(root){
  root.querySelectorAll('.hit').forEach(g => {
    g.addEventListener('mousemove', e => {
      tip.textContent = g.dataset.tip; tip.style.display = 'block';
      tip.style.left = e.pageX + 'px'; tip.style.top = (e.pageY - 10) + 'px';
    });
    g.addEventListener('mouseleave', () => tip.style.display = 'none');
  });
}
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
