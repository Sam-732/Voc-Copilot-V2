Voice-of-Customer tool for PMs. It pulls 13,000 Swiggy Play Store reviews, clusters them into complaint themes, ranks those themes by how many reviews mention them, and shows the actual reviews behind every claim — ordered by how many other users found them helpful.

Live dashboard: (add your GitHub Pages link here)

The point is not the summary. The point is that every theme resolves to the verbatim reviews it came from, so whoever reads it can check the claim instead of trusting it.

What it found

13,000 reviews from the Play Store, India, 16 Aug – 15 Sep 2026.

stage	count	what happened
Collected	13,000	30 days, scraped and stored untouched
With real content	4,126	68% were four words or shorter — "good", "worst", "👍"
Inside a theme	1,633	the rest fit no theme and were left in the reject bucket

Ten-plus complaint themes came out of that. The largest by volume:

Delivery runs far past the ETA, and cancelling costs money — 486 reviews
Support gives scripted non-answers, nobody takes accountability — 242
Fees stack up: platform, packaging, surge, delivery — 181
Refunds denied, partial, or delayed after a failed order — 144
Users explicitly switching to Zomato — 78

Three findings that surprised me:

Happy users say nothing. Of 7,162 five-star reviews, almost none are longer than four words. The ratings are strongly U-shaped — 84% are either 1★ or 5★ — so the usable complaint corpus is essentially the 1★ and 2★ pile.

Support failure is an amplifier, not a theme. In my hand-labelled sample, "customer care" was mentioned almost as often as delivery delay — but it was the primary complaint only 40% of the time it appeared. It rides on top of other failures and turns an annoyance into a one-star review.

Volume and agreement run in opposite directions. The most-upvoted review in any theme (150 "found helpful") sits in the smallest theme — hidden fees and subscription benefits not being honoured. The biggest theme's top review got 21. Within the themes, people write about delivery delays but endorse complaints about being overcharged. The most-upvoted review in the whole corpus — 2,590 helpful votes, about insects in the fries and support that brushed it off — was never clustered at all: HDBSCAN left it in noise. Both frequency ranking and clustering bury the complaints people endorse most.

*(Corrected 30 Sep 2026: an earlier version called the 150-vote review the most-upvoted in the whole corpus. It is the most-upvoted inside a theme; the corpus maximum is the unclustered 2,590-vote review.)*

What's wrong with it

This section matters more than the one above.

Clustering agreement is 21%. I hand-labelled 182 reviews, then measured whether the machine grouped them the way I did. When I said two reviews described the same complaint, it agreed 21% of the time. Reading the output confirms it: roughly a quarter of the reviews in any theme belong somewhere else, or belong equally to two themes. This narrows 13,000 reviews down to a dozen places worth looking. It does not classify reliably enough to act on without reading the evidence.

Reviewers are not users. They're a self-selected, mostly angry minority. This measures what people complain about publicly — not what users experience, and not what costs the business money.

Theme size is not incidence. 486 reviews mention delivery delay; that says nothing about how many customers were delayed. Different problems get written about at wildly different rates — normalised annoyances generate few reviews, things that feel like theft generate many.

Multi-complaint reviews are undercounted. About 18% of reviews describe two or more problems, but each review is filed under one theme only. The keyword signal table in the dashboard is the correction — it counts mentions across all themes, and shows chatbot complaints at more than double their theme size.

The model splits some themes by language. Romanized Hinglish clusters correctly within a theme — "extra charge bahut lagata hai" landed next to "charges are very high" — but delivery-delay complaints in Hindi and English formed separate clusters. Gujarati, Tamil and Bengali reviews collapsed into one undifferentiated cluster regardless of what they said, which I dropped.

Themes between 15 and 40 reviews are structurally invisible. My product rule said show anything above 15; the clustering parameter required 40. They were set independently and disagreed. A real complaint about inflated delivery distances (27 reviews) falls in that gap and never surfaces as a theme.

No per-review permalink. Play Store exposes no public URL for an individual review, so "citation" means the verbatim text, rating and date shown in the tool — not a link out.

Decisions I changed after measuring

The window. The spec said six months and 5,000 reviews. A 200-review recon pull showed Swiggy runs ~430 reviews/day, which makes those two numbers differ by a factor of twenty. Cut to 30 days.

Minimum theme size. Set to 15 before I had data. At 4,126 reviews that produced 63 fragments with one theme scattered across eleven of them. Raised to 40.

Ranking. Considered weighting by severity and by stated churn intent. Shipped plain frequency, because a rank I can explain in one sentence beats a rank I can't defend.

How it works
ingest.py    scrape 30 days of reviews into SQLite (append-only, resumable)
clean.py     drop <5-word reviews, normalise elongation, dedupe -> reviews_clean
cluster.py   embed -> UMAP (384d to 5d) -> HDBSCAN -> cluster labels
split17.py   re-cluster the one oversized theme on its own
themes.py    cluster ID -> theme name (hand-written; unlisted clusters are dropped)
eval.py      score clustering against the hand-labelled set
signals.py   keyword counts across themes
dashboard.py build the searchable dashboard

reviews_raw is append-only and never modified, so every citation resolves to an unedited source row.

Stack: google-play-scraper, SQLite, sentence-transformers (paraphrase-multilingual-MiniLM-L12-v2), umap-learn, hdbscan, pandas.

Running it
Verified on Python 3.14.3 with the versions pinned in requirements.txt.
The daily Task Scheduler task ("VoC Copilot daily") runs C:\Users\Samprat\AppData\Local\Programs\Python\Python314\pythonw.exe with run_daily_hidden.pyw - that is the Python 3.14 install path, so update the task's program after a Python upgrade.
bash
pip install -r requirements.txt
python ingest.py      # ~3 min, writes reviews.db
python clean.py
python cluster.py     # ~5 min, downloads a 450 MB model on first run
python dashboard.py   # writes dashboard.html

v2: daily pipeline and local app

The daily job (run_daily.bat, run by Task Scheduler at 21:00) fetches new reviews, places each one into the nearest existing cluster or the unassigned bucket (euclidean distance under 0.58), re-ranks the themes and rebuilds dashboard_v2.html. One-time setup before the first run: python bootstrap_v2.py (refits v1's UMAP, ~3 min). Decisions and findings are in labels/NOTES.md.

The local app shows the same data live and lets you record judgements:
python app.py         # then open http://127.0.0.1:5000/ ; stop with Ctrl+C
Pages: Themes (ranked complaint themes), Trends, Bucket, Search, Runs, What if (threshold preview, saves nothing), Flags, Label.
It only listens on 127.0.0.1, and only accepts forms from its own pages. It writes two things: flags (marking a placement wrong) go to the assignment_flags table in reviews.db, and labels go to labels/threshold_labels.csv only. Everything else is read-only. Flag saves pause while a daily run is in progress.
Data

reviews.db holds review IDs, text, ratings, dates and helpful-vote counts. No usernames are stored — the scraper collects them, the schema does not keep them. All reviews are already public on the Play Store.

reviews_labelled.xlsx is the hand-labelled golden set: 200 sampled reviews, 182 labelled by me, used to score the clustering. It's the one artefact in this repo that can't be regenerated by running a script.

See task progress for longer tasks.

README.md
dashboard.py
recon.py
Skills
dataviz
