"""
All tunable numbers for VoC Copilot v2 live here. Logic files import from this; they hold no literals.

v1 scripts (ingest.py, cluster.py, split17.py, ...) still carry their own hard-coded values.
Where v2 reuses a v1 value, it is repeated here and marked "v1". Change them together or not at all.
"""

# ---------- storage ----------
DB_PATH = "reviews.db"
LOG_DIR = "logs"
INGEST_LOG_FILE = "ingest.log"

# ---------- source (v1: ingest.py) ----------
APP_ID = "in.swiggy.android"
SCRAPE_LANG = "en"
SCRAPE_COUNTRY = "in"

# ---------- cleaning (v1: clean.py) ----------
MIN_WORDS = 5                    # v1: reviews shorter than this are dropped

# ---------- daily ingestion ----------
PAGE_SIZE = 200                  # v1: reviews per scraper request
PAGE_SLEEP_SECONDS = 1           # v1: pause between pages
MAX_PAGES = 100                  # safety stop, 100 x 200 = 20,000 reviews. If hit, the run fails
                                 # rather than inserting a partial window (which would leave a gap).
                                 # Raise it for a one-off catch-up after a long outage.
FETCH_RETRIES = 3                # attempts per page before the run is declared failed
RETRY_BACKOFF_SECONDS = 20       # wait = this x attempt number

# Anomaly band, in reviews per day covered by the run (fetched / days since last stored review).
# Normalising per day means a catch-up run after missed days is judged the same as a daily run.
# Expected volume is ~400-600/day; the band is deliberately wide so only real breakage trips it.
ANOMALY_MIN_PER_DAY = 150
ANOMALY_MAX_PER_DAY = 2000
MIN_DAYS_FOR_RATE = 1.0          # floor on days covered, so a same-day re-run isn't judged per-hour
RUN_STALE_HOURS = 2              # a run still marked 'running' after this long was stopped without recording;
                                 # the next run marks it failed (runstate.py)

# ---------- embedding + reduced space (v1: cluster.py) ----------
EMBEDDING_MODEL = "paraphrase-multilingual-MiniLM-L12-v2"   # v1
EMBED_BATCH_SIZE = 64                                       # v1
V1_EMBEDDINGS_FILE = "emb.npy"   # v1 artefact from split17.py, rows = reviews_clean in rowid order
UMAP_N_COMPONENTS = 5            # v1
UMAP_N_NEIGHBORS = 30            # v1
UMAP_MIN_DIST = 0.0              # v1
UMAP_METRIC = "cosine"           # v1
UMAP_RANDOM_STATE = 42           # v1
HDBSCAN_MIN_CLUSTER_SIZE = 40    # v1
HDBSCAN_MIN_SAMPLES = 10         # v1
HDBSCAN_METRIC = "euclidean"     # v1
HDBSCAN_SELECTION = "leaf"       # v1

# v1 history, not tunable: split17.py re-clustered cluster 17 into ids 100+.
# Used only by bootstrap_v2.py to check the refit UMAP reproduces v1's clusters.
V1_SPLIT_PARENT_CLUSTER = 17
V1_SPLIT_ID_START = 100
REPRODUCTION_MIN_ARI = 0.99      # bootstrap refuses to continue below this

MODEL_DIR = "models"
UMAP_MODEL_FILE = "umap_v1.joblib"

# ---------- incremental assignment ----------
# Distance from a new review to cluster centroids in the 5-d UMAP space.
# "euclidean" (Samprat, 29 Sep 2026): it's what HDBSCAN used to build v1's clusters, and v1 members
# land nearest their own centroid 97.2% of the time by euclidean vs 95.9% by cosine. Cosine was
# near-degenerate here: the UMAP cloud sits ~19 units from the origin with ~1-2 units of spread,
# so all cosine distances were ~1e-4. "cosine" is still available.
DISTANCE_METRIC = "euclidean"

# True (Samprat, 29 Sep 2026): centroids are computed once from v1 and never move, so assignments
# are reproducible while the threshold is being set. Bucket clusters get a centroid when found,
# which is also frozen. False = running-mean update as members join (the original spec).
FREEZE_CENTROIDS = True

# At or above this distance to the nearest centroid, a review goes to the unassigned bucket.
# 0.58 (Samprat, 30 Sep 2026), set from 10 hand-labelled borderline assignments
# (labels/threshold_labels.csv, labels/NOTES.md). The 4 "right" calls sat at 0.47-0.58 and the
# 6 "wrong" at 0.58-0.67; at 0.58, 3 of 4 rights are kept and all 6 wrongs go to the bucket.
# Caveat: 10 cases, all borderline by construction. 91.7% of v1 members sit under 0.58.
# Replaced placeholder 0.67 (v1 members' p95). After changing it: python assign.py --reset,
# then re-run assign.py.
ASSIGNMENT_THRESHOLD = 0.58

# Bucket re-clustering: when the unassigned bucket holds MORE than this many reviews, HDBSCAN
# (v1 parameters above) runs on the bucket alone. Spec: same as min_cluster_size.
BUCKET_RECLUSTER_MIN = HDBSCAN_MIN_CLUSTER_SIZE
EMERGENT_CLUSTER_ID_START = 1000  # clusters found in the bucket get ids from here, clear of v1's

ROUNDTRIP_SAMPLE = 400           # bootstrap diagnostic: v1 members re-projected as if new
ROUNDTRIP_SEED = 0

# ---------- assignment log / review (phase 3) ----------
EXPORT_DIR = "exports"
REVIEW_DEFAULT_N = 20            # rows shown by `assignlog.py weakest` / `ambiguous` without -n
TEXT_PREVIEW_CHARS = 160         # review text shown per row in the terminal (CSV always has full text)

# ---------- hand-labelling batches (assignlog.py labelset) ----------
LABELS_DIR = "labels"
LABELSET_WEAKEST_N = 5           # picked evenly across the weakest-fit quantile below
LABELSET_AMBIGUOUS_N = 5         # smallest nearest-vs-2nd gaps
LABELSET_WEAK_PERCENTILE = 75    # "weakest" = assigned reviews at or above this distance percentile
LABELSET_SEED = 7                # shuffle order, so position doesn't hint at the pool

# ---------- ranking + dashboard (phase 4) ----------
WEAK_SIGNAL_MIN = 25             # v1 rule: clusters under this many reviews show counts only,
                                 # never percentages, and are labelled "weak signal"
RECENT_DAYS = 7                  # recency column: reviews dated within this many days of the newest
VOLUME_BIN_DAYS = 7              # volume-over-time bars: one bar per this many days
DASHBOARD_V2_FILE = "dashboard_v2.html"
DASHBOARD_REVIEWS_PAGE = 20      # reviews rendered per click when a cluster is expanded

# ---------- themes (themes_v2.json) ----------
THEMES_V2_FILE = "themes_v2.json"
# A sub-theme (cluster) inside a theme is flagged "most endorsed" when its share of the theme's
# helpful votes is at least ENDORSEMENT_LIFT times its share of the theme's reviews, and it has
# at least ENDORSEMENT_MIN_VOTES votes (Samprat, 30 Sep 2026). Display only - never changes rank.
ENDORSEMENT_LIFT = 2.0
ENDORSEMENT_MIN_VOTES = 20

# ---------- cluster review: purity samples + cards (assignlog.py purityset / card) ----------
PURITY_SAMPLE_N = 10             # random members of a cluster to hand-check
PURITY_SEED = 11
CARD_TYPICAL_N = 10              # reviews closest to the centroid shown on a card
CARD_ENDORSED_N = 5              # most-helpful reviews shown on a card
CARD_NEAREST_N = 3               # nearest other clusters listed on a card

# ---------- local web app (app.py, phase 4) ----------
APP_HOST = "127.0.0.1"           # localhost only - the app is never reachable from other machines
APP_PORT = 5000
DB_BUSY_TIMEOUT_S = 5            # how long a request waits if the daily job is mid-write
FLAG_NOTE_MAX_CHARS = 500        # longest note accepted on a flag
SEARCH_MIN_CHARS = 2             # shorter search terms are ignored
SEARCH_MAX_RESULTS = 200         # most results shown for one search
BUCKET_TYPICAL_N = 5             # typical reviews shown per bucket-found cluster in the bucket browser

# ---------- "what if" threshold preview (app.py /whatif, phase 4 step 3) - read-only ----------
WHATIF_MIN = 0.05                # accepted threshold range in the form
WHATIF_MAX = 3.0
WHATIF_OVERRIDE_DISPLAY_IDS = (2, 3)   # sub-themes the optional second threshold applies to (the wide delivery ones)
THRESHOLD_LABELS_FILE = "labels/threshold_labels.csv"
# hand purity labels: file, the display id of the cluster sampled, and the theme a "yes" belongs in
PURITY_LABEL_SOURCES = [
    {"file": "labeling cluster 31.txt", "cluster": 31, "yes_theme": "delivery_late"},
]

# ---------- in-app labelling (app.py /label, phase 4 step 5) - writes labels/threshold_labels.csv only ----------
# Failure types offered when labelling, taken from the ones used in the first 10 labels (labels/NOTES.md).
LABEL_FAILURE_TYPES = [
    "none (positive example: confident and correct)",
    "near-tie between two wrong clusters; no right cluster exists",
    "confidently wrong: correct cluster exists but ranked 2nd by a wide margin",
    "correct cluster exists but ranked second (small gap)",
    "multi-issue review: assignment picked the secondary issue",
    "right answer, wrong reason: placed by language, not content",
    "other (describe in the reason)",
]
LABEL_REASON_MAX_CHARS = 1000    # longest reason accepted
