# Assignment findings: threshold labelling, 29-30 Sep 2026

> **Cluster numbers in this file are internal ids** - the ones stored in `reviews.db` and in the
> `cluster` / `second_cluster` columns of `threshold_labels.csv`. On 30 Sep the clusters were
> renumbered for display (`themes_v2.json`); the dashboard shows the display id. The label CSV
> now carries both (`cluster_display_id`, `second_display_id`). Every id mentioned below:
>
> | Internal (this file) | Display (dashboard) | Theme |
> |---|---|---|
> | 1 | 24 | Positive |
> | 2 | 22 | Positive |
> | 3 | 23 | Positive |
> | 4 | 29 | Positive |
> | 6 | 27 | Positive |
> | 7 | 25 | Positive |
> | 8 | 21 | Language clusters, unsorted |
> | 9 | 28 | Positive |
> | 10 | 26 | Positive |
> | 12 | 20 | Language clusters, unsorted |
> | 18 | 4 | Delivery runs far past the ETA, and cancelling costs money |
> | 19 | 8 | Refunds denied, partial, or delayed after a failed order |
> | 20 | 2 | Delivery runs far past the ETA, and cancelling costs money |
> | 21 | 3 | Delivery runs far past the ETA, and cancelling costs money |
> | 22 | 9 | Refunds denied, partial, or delayed after a failed order |
> | 100 | 7 | Fees, pricing and offers |
> | 101 | 17 | Restaurant sends the wrong item or the wrong size |
> | 102 | 13 | Spoiled or substituted items, refund is the only remedy offered |
> | 104 | 15 | Order delayed, then cancelled by the platform |
> | 105 | 11 | Food quality: missing, short or spoiled items |
> | 106 | 19 | General anger, no specific issue |

Notes kept next to `threshold_labels.csv`. Setup: v2 incremental assignment, euclidean distance
in v1's 5-d UMAP space, centroids frozen at v1, placeholder threshold 0.67. Corpus:
1,841 new cleaned reviews (14 Sep evening to 28 Sep), 1,449 assigned, 392 unassigned.

## Known limitation: complaints leaking into praise clusters (accepted, not fixed)
Eight of the 12 clusters not named in `themes.py` are praise (1, 2, 3, 4, 6, 7, 9, 10;
v1 members average 4.3-4.8 stars). v1 left them out of `themes.py` on purpose, which keeps them
off the complaint dashboard.

- 300 of the 1,449 assigned new reviews (21%) went into them, averaging 4.46 stars
  (v1 members of the same clusters: 4.53).
- 26 of the 300 are 1-2 stars (1.8% of all assigned). Several are real complaints that will
  never reach the complaint dashboard, e.g. "Stop using AI images for the food!" (cluster 1)
  and "ordered Chili chicken 8 pieces but they give me 6" (cluster 1).
- Cluster 3 (food-quality praise) leaks the most: 8 of its 38 new reviews are 1-2 stars.

Decision (Samprat): tolerable as a known limitation at 1.8%. Documented, not fixed.

## Shared theme names are v1's hand merges, not shifting IDs
`themes.py` maps several cluster IDs to one name. That's v1's merge decision:
- 12, 18, 20, 21 -> "Delivery runs far past the ETA, and cancelling costs money"
- 19, 22 -> "Refunds denied, partial, or delayed after a failed order"

The IDs are stable. All centroids are the v1 ones (origin `initial`, frozen), their sizes match
v1's `clusters` table exactly, and two full re-runs gave identical assignments.

The merged clusters are separate places in the space. Euclidean distances between centroids:
18-20 1.33, 18-21 2.39, 20-21 2.41, 19-22 2.44. For scale, the median distance between any two
centroids is 3.43, and a v1 member sits a median 0.28 from its own centroid.

Consequence for labelling: the cluster ID decides a placement, so the same theme name can be
right under one ID and wrong under another. Review 2 went to 21 (delivery) when 22 (refunds)
fit. Review 3 went to 20 (delivery), which was correct.

## FLAG: cluster 12 is romanized Hindi, not delivery delay
Cluster 12 sits 6.7-7.7 from the other three "delivery" clusters. Its most typical v1 reviews
are romanized Hindi on mixed topics, e.g. "dekhta hu agle agle baar acha reting dunga" and
"bahut dhokha diya jata hi dikhta kuch hai lata kuchh hai". This matches the PRD's A3 finding
that themes split by language. Mapping it to the delivery theme is a v1 naming question, not
something assignment can fix.

Flagged (Samprat): a Hindi-in-Latin-script cluster carrying a delivery label means every review
that lands in it inherits a wrong theme. 56 new reviews went there (mean 2.75 stars). Some are
about delay ("oder bahot let aaya hai") and many aren't: food praise ("Ujjain mein sanvariya
sandwich ka test bahut shandar hai"), bad food with no response, general complaints. All 56
count towards "Delivery runs far past the ETA" on any dashboard that uses themes.py.

## Failure types seen in hand labelling
Recorded per review in `threshold_labels.csv` (column `failure_type`):
1. **Near-tie between two wrong clusters; no right cluster exists** (reviews 1 and 5). The
   threshold can't fix this; the right theme isn't in the structure. Review 5 was item condition
   on arrival (dirty, not chilled) plus delivery-partner conduct, split 0.001 apart between
   105 and 106.
2. **Confidently wrong: correct cluster exists but ranked second by a wide margin** (review 2:
   0.62 wrong vs 0.77 right). A tighter threshold would send it to the bucket, not to the
   right cluster.
3. **Multi-issue review: assignment picked the secondary issue** (review 4: wrong item was
   the core complaint, cancellation language pulled it to 20; the right cluster, 101, ranked
   5th of 30 at 1.77). This is the PRD's A4 limitation (one theme per review) showing up in
   assignment.
4. **Right answer, wrong reason: placed by language, not content** (review 5591: a Hinglish
   delay complaint, correct theme, but it landed in 12 at a near-tie with 8, 0.4681 vs 0.4704 -
   both language clusters). No threshold can catch this.

## Outcome (30 Sep 2026)
- Threshold set to **0.58** from the 10 labels (4 right at 0.47-0.58, 6 wrong at 0.58-0.67).
- Cluster 12 moved out of the delivery theme into "Language clusters, unsorted" (`themes_v2.json`).
- 11 complaint themes after consolidation; see `themes_v2.json` for who decided each grouping.

## Known issue: bucket-cluster ids reused before 30 Sep (fixed going forward)
Until 30 Sep, `assign.py --reset` deleted bucket clusters' centroids and the next bucket
re-cluster restarted at 1000, so a new cluster could take a discarded cluster's number.
943 `assignment_log` rows written before the current reset (runs up to 61, cosine and earlier
euclidean eras) name 1000+ ids that today belong to different clusters. Those rows can't be
resolved to today's clusters; read any 1000+ id in runs before the latest reset as "a bucket
cluster from that era". The 10 labelled records don't involve any 1000+ id.
Fix: ids are now allocated above the highest id ever logged, so after a reset rediscovered
bucket clusters get new numbers (tested: 1004-1007 on a copy) and need mapping afresh.

## Bucket clusters 30 and 31 (display ids), 30 Sep 2026
Checked before promoting anything. Display 30 = internal 1003, display 31 = internal 1001.

**One global threshold doesn't fit every cluster.** v1 clusters' own spread varies ~5x: p95 member
distance runs from 0.17 (a praise cluster) to 0.91 (delivery sub-theme 3). At 0.58, 22% of
sub-theme 3's and 32% of sub-theme 2's own v1 members would be rejected.

**Cluster 31 looks like delivery overflow.** Centroid 0.39 from delivery sub-theme 3. Of the 47
reviews it formed from, 27 were nearest sub-theme 3 when bucketed, 25 of them inside that
cluster's own p95 radius (0.91). Stable: 78-80% of its members re-form together whether the 14
days are replayed as one run, every 2 days or every 3 days. Purity sample: purity_cluster31.csv.

**Cluster 30 is not a stable theme.** Founding members sit a median 1.41 from its centroid (p95
2.13), wider than any v1 cluster (max p95 0.91), so no later review ever gets within 0.58 and it
stopped growing the day it was found (250 reviews, all dated 14-24 Sep). Replaying the same days
on other schedules recovers only 22% / 66% / 44% of its members as one cluster, and pairs that
stay together under one schedule stay together under another only 35-41% of the time. Its
membership reflects when the bucket was re-clustered, not a fixed group.
Keyword check: only 52 of its 250 members mention a delivery partner or non-delivery.

**Delivery-partner complaints are real but not new.** By keyword, 12.1% of v1 reviews and 12.6%
of new reviews mention a delivery partner or non-delivery - a steady rate, not an emerging one.
v1 couldn't see them as a theme: 275 of v1's 501 such reviews went to noise.

**Design finding (v2):** re-clustering only what's left in the bucket is path-dependent - each
new cluster drains the bucket, so what forms next depends on run timing. Candidate fixes (not
applied): re-cluster all reviews that ever went unassigned, not just the remainder; and/or only
surface a bucket cluster after it re-forms in N consecutive runs.

## Threshold analysis after cluster 31 purity labels (30 Sep 2026) - NOT APPLIED
Labels: Samprat, 10 random members of cluster 31 (display id), saved as
`labeling cluster 31.txt` in the repo root. Question: is this primarily a late-delivery /
cancellation complaint? No reasons were filled in.

**Result: 4 yes, 6 no - 40% purity.** Cluster 31 is not clean delivery overflow. The geometry
(0.39 from delivery sub-theme 3, stable across schedules) overstated it: the "no" reviews are
app and tracking bugs, support non-answers, a double order from OTP auto-read, and a UX complaint
about the default tab.

Where the 10 sit (nearest v1 cluster, by display id): only 3 are nearest a delivery sub-theme
(all sub-theme 3); 5 are nearest General anger (18), 2 nearest the chatbot cluster (16), 1 nearest
refunds (9). Two of the four "yes" are nearest General anger, so no delivery cut-off would ever
reach them.

What each option would do to these 10:

| Option | Placed | Right | Wrong |
|---|---|---|---|
| Keep 0.58 globally | 0 | 0 | 0 |
| Per-cluster cut-off (each cluster's v1 p95) | 4 | 2 | 2 |
| 0.58 + exception for delivery sub-themes 2 and 3 (their p95: 0.81, 0.91) | 3 | 2 | 1 |

Adding the threshold labels: the exception would also re-admit reviews 5509 (sub-theme 3, 0.62)
and 3888 (sub-theme 2, 0.67), both judged wrong. Across all labelled cases it touches, the
exception gains 2 right placements and adds 3 wrong ones.

Claude Code's argument for keeping 0.58 (input, not the decision):
- Neither loosening option places delivery complaints more accurately than it misplaces other
  reviews: 50% and 67% on 3-4 cases, and worse once the threshold labels are included.
- The failure costs aren't symmetric. A review left in the bucket is visibly unplaced and still
  counted on the dashboard; a wrongly placed review silently inflates a theme's count.
- Cost of keeping 0.58, accepted: some real delivery complaints stay in the bucket (2 of these
  10 were near delivery and judged "yes").
- The samples are small (10 purity + 10 threshold labels); one flipped verdict changes the
  per-option numbers. This is a "don't change yet", not a settled answer.

The finding from 30 Sep still stands, just smaller: cluster spread varies ~5x, so one global
number can't fit every cluster. **Untested hypothesis:** the wide delivery clusters may be wide
partly because they're mixed, in which case widening their cut-off would admit the mix too. The
cluster 31 labels don't test this: cluster 31 is not a delivery sub-theme. See "Status before
pause", item 4, for what would.

**To revisit:** label ~20 reviews sitting 0.58-0.91 from delivery sub-themes 2 and 3 (the only
band the exception would change), then re-decide. Also still open: purity_cluster30.csv is
unlabelled, and bucket re-clustering is path-dependent (see above).

## Status before pause (30 Sep 2026)

### 1. File mix-up
"labeling cluster 30.txt" contained the cluster 31 purity labels (all 10 review ids match
purity_cluster31.csv; none match purity_cluster30.csv). Renamed to "labeling cluster 31.txt".
Cluster 30 has no labels yet: purity_cluster30.csv is unlabelled.

### 2. Missing reasons
purity_cluster31.csv has empty verdict and reason columns; the verdicts exist only in
"labeling cluster 31.txt", and its reason column is blank too. The cluster 31 reasons need
redoing.
The four failure types were recorded with reasons for the 10 threshold labels, in
threshold_labels.csv (columns failure_type and reasoning, all 10 filled) and summarised in
"Failure types seen in hand labelling" above. They do not come from the purity labels.

### 3. Threshold status
Decision (Samprat): keep 0.58 for now. Provisional - based on 20 labels (10 threshold, 10
cluster 31 purity).
Counter-argument (Samprat): bucketed reviews concentrate in the wide delivery clusters, so
delivery themes are systematically undercounted in the ranking unless the dashboard shows the
bucket size next to them.
Data, 1,841 placement decisions since the last reset (545 bucketed, 30% overall), share of
reviews nearest each cluster/theme that went to the bucket:
delivery sub-theme 2: 51% (69 of 134) · delivery sub-theme 3: 49% (57 of 117) ·
Refunds: 48% · Food quality: 55% · Fees: 28% · Support: 18% · Positive: 11%.
Delivery 2 and 3 are the largest source of bucketed reviews (126 of 545), but Refunds and Food
quality lose a similar share, so the undercount is not delivery-only.
(Counts include reviews later moved from the bucket into bucket clusters.)

### 4. Open check: is cluster 31 one of the delivery sub-themes?
No. Cluster 31 (internal 1001) is a cluster found in the unassigned bucket, grouped under
"Found in the unassigned bucket". Its centroid is 0.39 from delivery sub-theme 3, but it is not
part of that sub-theme.
So "delivery clusters are wide because they're mixed" has no direct evidence yet - the cluster
31 labels measure cluster 31, not sub-themes 2 and 3.
The direct test is a purity sample of sub-themes 2 and 3 themselves, or the next labelling
round below. purity_cluster30.csv tests whether cluster 30 is one coherent complaint; cluster
30's nearest themes are Food quality (1.08) and Refunds (1.13), not delivery.

### 5. Next labelling round
About 20 reviews at distance 0.58-0.91 from delivery sub-themes 2 and 3 (the only band the
delivery exception would change).
Decision rule, written before labelling: "If at least ___ of 20 clearly belong, I adopt the
exception."
Fill in the reason column for every label.

### 6. What "depending on run timing" means
The bucket is re-clustered only on what's left in it, and each cluster found removes its
reviews, so which clusters form depends on how many days had piled up at each run. The same
reviews replayed on a different schedule re-formed only 22-66% of cluster 30.

### 7. Dashboard counts are provisional
Daily runs use the provisional 0.58 threshold and the unfixed bucket re-clustering (item 6), so dashboard counts are not final.

### 8. Phase 4 framework (Samprat, 30 Sep 2026)
Flask. Dropped the 'match v1's dashboard stack' rule: the dashboard now needs to write flags and labels, which a static page can't do.

### 9. First scheduled run, 1 Oct 2026: assign interrupted (run 77 changed by hand)
The task started at 21:00. Ingest run 2 succeeded (884 fetched, 880 new raw, 296 new clean).
Assign run 77 started at 21:00:12 and was stopped by Ctrl+C or the console window closing
(exit 0xC000013A; logs/assign.log holds only "^C"), so rank and the dashboard rebuild never ran.
Nothing was written - assign commits in one transaction at the end - but run 77 stayed
'running', because the failure handler caught Exception and Ctrl+C isn't one.
Changed by hand: run 77's status set to 'failed' with that explanation, so it doesn't look like a
run in progress. Fixes: interrupted runs are now recorded as failed, the next run marks stale
'running' rows as failed (runstate.py), and the task now starts without a window
(run_daily_hidden.pyw). The 296 reviews were assigned in a manual catch-up run the same evening.

### 10. Flagging: what can and cannot be flagged (step 4, 1 Oct 2026)
v1's original 4,126 reviews can't be flagged: a flag attaches to one daily placement decision (an assignment_log row), and v1's members were placed by v1's own clustering, so they have no decision to flag. Reviews never clustered (under 5 words or duplicate text) can't be flagged either. Flags are written only through actions.py, by both the app and `assignlog.py flag`, into assignment_flags, which resets never clear.
