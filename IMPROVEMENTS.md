# Improvement backlog

_From a full design review on 2026-09-12. Ordered by leverage, not effort._

**Tier 1, Tier 2, and #13 Timezone are complete.** 13.5 of the 15 items are
done (see [§ Done](#done) at the bottom for what they were and what the review
got wrong).

**Deliberately parked:** #6 `atr_pct` and #7 promoter pledge. Neither blocks
the system. #6 is logging; when enough trades settle (4–6 weeks), the data will
say which direction to score it. #7 has no free source; a hand-curated blocklist
is 80% as effective and requires zero maintenance.

Open: **#12 and #14 in Tier 3**. #15 Survivorship has been documented as per the
review's recommendation.

**Combined effect of #4 and #5, measured.** For one mid-quality setup (pattern
confidence 60, RS 7.5, tightness 0.5, flat sector) the old score was **71.5
regardless of volume or extension** — both signals were inert:

| vol ratio | extension | old | new | alerts at 60? |
|---|---|---|---|---|
| 1.5× | 1% | 71.5 | 64.0 | yes → yes |
| 1.5× | 5% | 71.5 | 59.0 | yes → **no** |
| 1.5× | 8% | 71.5 | 54.0 | yes → **no** |
| 2.0× | 5% | 71.5 | 61.5 | yes → yes |
| 3.0× | 1% | 71.5 | 71.5 | yes → yes |
| 3.0× | 8% | 71.5 | 61.5 | yes → yes |

Only a 3× breakout taken within 2% of its pivot keeps the old score.
**`min_score_to_alert` is deliberately still 60** — re-tune it from
`alert_features` once trades accumulate, not from another prior.

The ordering principle: **an item that makes the paper-trade edge estimate
honest outranks an item that adds a signal**, because the edge estimate is what
decides whether real capital goes in. And anything that improves signal accuracy
sits behind the feature log, since without outcome data any re-weighting is just
swapping one guess for another.

---

## Tier 1 — makes the go-live number honest

Both items shipped on 2026-09-12 — see [§ Done](#done).

---

## Tier 2 — signal accuracy

These are worth doing, but the feature log (`alert_features`) now records the
evidence to calibrate them. Prefer "log it, then tune from the sample" over
picking a number today.

### 3. Market-regime gate — done 2026-09-12, see [§ Done](#done)

### 4. Volume component saturates — done 2026-09-12, see [§ Done](#done)

### 5. Extension past the pivot is unscored — done 2026-09-12, see [§ Done](#done)

### 6. Score two logged-but-unscored signals — half done 2026-09-12

`close_in_range` is now scored (see [§ Done](#done)).

**`atr_pct` deliberately left unscored**, and this is a judgement worth
re-examining rather than a task left undone. Unlike close-in-range it has no
agreed direction: high volatility is either the fuel for a fast move or the
noise that shakes you out, and which one depends on the name. It is also
already priced in twice — the stop is `1.5 × ATR` below the level, so a
volatile stock automatically gets a wider stop and a smaller position for the
same rupee risk. Scoring it today would mean inventing a sign, which is exactly
what this file's ordering principle says not to do. It keeps accumulating in
`alert_features`; revisit when `paper.stats` can say which way it points.

### 7. Dead gates — half done 2026-09-12, see [§ Done](#done)

Two of the three turned out not to need fixing at all; the third has no source
to fix it with. What shipped is the *visibility*, plus the measurements that
settle which is which. The one genuine remaining hole is **promoter pledge**.

### 8. No concentration control — done 2026-09-12, see [§ Done](#done)

---

## Tier 3 — measurement and operations

### 9. Partial-day volume vs full-day averages — done 2026-09-12, see [§ Done](#done)

### 10. Stale bars still get analysed — done 2026-09-12, see [§ Done](#done)

### 11. Backtest is a smoke test, not evidence — done 2026-09-12, see [§ Done](#done)

Original text kept below for the record.



Survivorship-biased (replays today's NIFTY 500 membership over history),
non-overlapping trades (`backtest.py` resumes past each exit, discarding signals
that fire during a held trade and biasing the sample), omits RS and sector, no
regime gate. (Costs and gap fills are now modelled — see § Done.) Fine as a
regression check on the detector wiring; the
GUIDE should say plainly that the numbers are not an edge estimate.

### 12. No job-failure notification

Three time-critical jobs a day, and a failure — or a laptop asleep at 9:30 — is
discovered only by reading logs. The 2026-09-10 and 09-11 `eod_settle` crashes
went two days unnoticed, which is the argument. A Telegram message on any
`FAILED` row in `run_log`, plus a daily "did all three run?" check, is cheap and
the channel already exists.

### 13. Timezone

Everything uses `date.today()` on the local clock. Pin to Asia/Kolkata so the
trading-day logic doesn't depend on machine timezone or a run near midnight.

### 14. Storage

`prices` is the bulk of the workbook and the part humans never read by hand.
SQLite for prices plus an Excel *export* for the sheets you actually inspect
would be faster and typed — the `Store` API is already abstract enough to make
it a contained change, and the untyped-Excel dtype crash of 2026-09-10 was a
symptom of exactly this. Not urgent: `Store.save()` already writes atomically
(temp file + `os.replace`) with lock retries, so the durability risk is handled.

Still worth doing: **move `data_cache/` out of the OneDrive-synced folder**, so
sync can't hold a lock mid-run.

### 15. Survivorship in the universe — documented 2026-09-23

The `universe` sheet is overwritten weekly with today's NIFTY 500, and there's
no point-in-time membership. This inflates backtest results and can't be fixed
cheaply (historical index membership isn't freely available). The review's
own conclusion: document rather than solve.

**What this means:** The backtest replays *all signals from today's NIFTY 500
over history*, so every name in the sample survived and stayed in the index.
Any company that:
- was delisted or downgraded out of the NIFTY 500 during the lookback,
- suffered a catastrophic decline that wiped out the setup,
- went through a demerger or recapitalization mid-holding,

...is absent from the backtest. The effect is one-directional: we see only the
names that *didn't* collapse, inflating win rate and profit factor.

**Why it's not fixed:** Historical point-in-time NIFTY 500 membership is not
freely available. NSE publishes index changes (add/delete announcements) but
doesn't provide a downloadable archive of past membership. Building one would
require:
- Scraping NSE announcements from 2024 onward,
- Reconstructing the exact membership on each rebalance date,
- Tying it to the price history (not trivial — a stock's symbol may change
  across a delisting/relisting cycle).

This is a data archaeology task, not a code change. The forward paper log
(`paper_trades` table after 4–6 weeks of running) answers the survivorship
question honestly because it contains only trades the live scanner actually
took. **That is the real edge estimate.** The backtest is purely a regression
check: "did the pipeline still fire as it did last week?"

See `GUIDE.md` § 12 for the full list of backtest limitations.

---

## Done

### Position sizing had no capital constraint — 2026-09-12

**Not a backlog item. Found while doing #11, and the most serious defect this
review process has turned up.**

`position_size()` was purely risk-based, with no notion of what a position
*costs*:

    position value = risk_amount / (stop distance as a fraction of price)

so a 2.5% stop with 2.5% capital risk buys exactly 100% of the account in one
trade, and anything tighter buys more than the account holds.

Measured across 94 real setups from cached prices: **median position 85% of
capital, mean 96.5%, 30 of 94 over 100%, all 94 over 25%, largest ₹6,84,607
against a ₹2,00,000 account (342%)** — a GRASIM entry at ₹3,140 with the stop
₹23 below it, so risk sizing bought 218 shares.

This was not a backtest artefact. `insert_alert` used the same function, so the
paper log recorded impossible positions and the share count printed in every
alert was one the account could not have paid for.

Fixed: `position_size(..., max_value=)` plus
`max_position_value(capital, max_concurrent_positions)` = capital ÷ slots
(₹25,000 here), so a full book is exactly 100% of capital. Derived from
`max_concurrent_positions` rather than configured separately — "8 concurrent
positions" only means something if eight of them fit. The cap is a ceiling;
below it risk-based sizing still governs.

Effect on the 60-day backtest over the same 94 setups (the rupee figures were
the symptom that exposed it):

| | before cap | after cap |
|---|---|---|
| costs | ₹62,858 | **₹11,408** |
| gross P&L | −₹15,914 | −₹514 |
| net P&L | −₹78,772 | **−₹11,922** |

`avg R` is unchanged at −0.25R, and correctly so: R is a per-share quantity and
`cost_per_share` is computed on a one-share notional, so the cap changes what a
trade *costs in rupees*, not its R-multiple. The two moving independently is the
expected signature, and a useful check that the cap did what it claims.

**This also invalidates a premise in the original review.** #7 argued the ADV
floor was non-binding because "with ₹2L of capital, positions are ~₹5–15k".
Positions were in fact ₹1.2–6.8 lakh. The ADV conclusion survives — even a
₹2.7 lakh position is under 1% of a day's turnover in the thinnest constituent —
but the "~250×" headroom figure quoted in § Done for #7 was computed from the
wrong position size and is nearer 120×. Still not binding; the reasoning was
lucky rather than right.

### Backtest health warning — 2026-09-12

`backtest.py` now carries the verdict in its module docstring and prints a
`CAVEATS` block under its own output on every run — placed *after* the numbers,
so it is read by someone who has just seen a win rate and is deciding what it
means. GUIDE § 12 leads with the same point.

Two of the review's five objections are now stale and were corrected: costs and
gap-through-stop fills **are** modelled (#1, #2), as is the capital cap, and all
three match the live tracker exactly. Two objections stand as written
(survivorship, non-overlapping trades), one was expanded — RS and sector score 0
and 'flat', which means backtest scores sit *below* live scores, so a setup that
clears `min_score_to_alert` live may not clear it here — and a fifth was added:
the backtest applies no concentration limits, so it takes every signal where the
live scanner would not.

Deliberately not "fixed" beyond the warning. Making the backtest a real edge
estimate needs point-in-time index membership, point-in-time breadth and a
portfolio simulator; the forward paper log answers the same question honestly
and is already running.

### Stale bars excluded — 2026-09-12

Freshness is re-checked **after** the fetch in `morning_scan`, and stale symbols
are dropped from both the scan and the RS cross-section. The cross-section
matters as much as the scan: a symbol frozen at last week's price carries a
stale 63-day return that distorts every *other* symbol's RS percentile.

**The item's prescription — "skip any symbol whose latest bar isn't today" — is
wrong now, and following it literally would have broken the scanner.** Since #9
the morning scan deliberately excludes today's partial bar, so at 9:30 the
newest bar it should ever see is *yesterday's*. Requiring a bar for today would
have rejected the entire universe and produced zero setups every morning.

The correct yardstick is the last session that actually closed, so
`trading_calendar.last_completed_session()` was added — today once the close has
passed, otherwise the previous trading day, skipping weekends and holidays,
read in IST so a scheduler in another timezone can't mislabel yesterday as
today. `previous_trading_day()` came with it, bounded so a bad holidays file
can't loop forever.

The requirement genuinely differs by job, which is why one rule doesn't fit:
morning wants the last completed session; the pre-close scan wants **today's**
bar (it is asking "did this break out today" — confirming against yesterday's
close would open a position on a move that may already be over), and that check
was missing there too, in both the breakout and pullback loops; `eod_settle`
already had it.

Measured on the live cache: **493 of 500 symbols fresh, 7 excluded** — frozen a
day behind after failed fetches, and precisely the ones that were previously
being scanned against old prices.

### Partial-session volume, measured rather than assumed — 2026-09-12

New `analysis/session.py`. The review suggested session-fraction scaling "or
better, a typical volume-by-3PM profile" — the profile was measured, because
clock-time scaling turns out to be wrong in a way that matters.

**Measured over 276 symbol-days of 5-minute bars across 12 large NSE names:**

| Time | Measured | Clock time |
|---|---|---|
| 09:30 | 0.039 | 0.040 |
| 11:30 | 0.316 | 0.360 |
| 13:30 | 0.609 | 0.680 |
| 14:30 | 0.747 | 0.840 |
| **15:00** | **0.851** | **0.920** |

NSE volume is U-shaped, so the closing half hour carries ~15% of the day in 8%
of its minutes and clock time overstates progress all session long. The review's
own figure (~91%) was the clock-time one. The real number at 3 PM is **85%**,
which means **a genuine 1.5× volume day read as 1.27× and failed the gate** —
a bigger effect than "modest but one-directional" suggested, and it compounds
with #4 now that the volume score actually discriminates.

**The two scans needed opposite fixes**, which was the non-obvious part:

- **Pre-close (~85% done)** — project to a full-day estimate. Stable base.
- **Morning (9:30, ~4% done)** — **drop the partial bar entirely.** This was a
  worse bug than the one the item describes and went unmentioned in the review:
  a 15-minute bar against a 20-day full-day mean reads as a ~0.04× volume ratio,
  so *every* morning candidate was scored on a volume figure that was pure
  clock artefact. Projecting from 4% would multiply the opening fifteen minutes
  by ~26 — noise amplification, not bias removal. `MIN_PROJECTABLE_FRACTION`
  (0.5) separates the two behaviours.

The profile is medians over liquid large caps; thin names will differ and the
figures drift with market structure. Far better than the implicit 1.00 that was
there before, but not a precise constant.

Also pins the session clock to **Asia/Kolkata** regardless of the machine's
timezone — a down payment on #13.

### Concentration control, and three other dead settings — 2026-09-12

All four parts of this item were the same disease: a setting that exists, is
documented, and does nothing.

**Concentration limits** — new `filters/concentration.py`, applied at entry.
`max_concurrent_positions: 8` (which had no call site at all) plus a new
`max_positions_per_sector: 3`. The sector cap is the one that matters: breakouts
cluster, so without it eight positions can be eight pharma names — one bet at
20% of capital dressed as eight at 2.5%.

The pre-close scan now runs in **two passes** — collect every confirmation, then
open positions — because slots must go to the highest-scoring candidates.
Processing in watchlist order would hand the day's capacity to whichever symbol
happened to sort first, which is not a decision anyone made. Open positions
consume both budgets so limits hold across days; pullback entries consume a slot
too; `0` disables either limit; rejections are logged loudly as *capacity, not
quality*, because a long rejection list means the limits or the capital are
wrong and that should be visible rather than inferred.

Unmapped industries get a per-symbol bucket rather than a shared "unknown" one —
two unclassifiable names are unknown, not known to be alike.

**`max_favorable` / `max_adverse`** — now written on every settle by
`tracker.compute_excursions`, in R, from bars strictly after the entry day, and
recomputed rather than accumulated so re-runs stay idempotent. These answer the
two questions realised P&L cannot: *max_favorable 3.5R on a trade that exited at
+2R* says the target is too near; *max_adverse −0.9R on a trade that went on to
win* says the stop is barely surviving.

**`alert_ttl_days`** — deleted. It governed `ALERTED -> CANCELED` under the old
next-day-open entry model and has been unreachable since trades started opening
as `ENTERED` at confirmation.

Deleting a config key would normally crash every existing `config.yaml` with an
opaque `TypeError: unexpected keyword argument`, so `config._known_fields` now
filters each section to its declared fields. Unknown keys are dropped **with a
warning** rather than silently: a dropped key is usually a retired setting, but
it is occasionally a typo, and a typo that vanishes without a word is how a
threshold quietly stops applying. Verified both ways — current config loads
clean, a config carrying `alert_ttl_days` plus a deliberate typo loads with two
warnings and the correct values intact.

Stale GUIDE sections describing the old `ALERTED → next-day-open` state machine
were corrected in passing; they had been wrong since the entry-model change.

### Dead gates — made visible, and two of them dismissed — 2026-09-12

New `filters/gate_audit.py`, logged by the morning scan every run. It is a
report, never a gate. Each filter is classified ACTIVE / INERT / REDUNDANT, and
the run ends with an explicit "N gate(s) INERT — treat the protection they imply
as absent". A filter that stops working now surfaces the next morning instead of
at the next design review.

**The measurements, which is the part that actually settles this item:**

- **Market cap — REDUNDANT, not worth building.** Probed the 15
  lowest-turnover NIFTY 500 names: market caps run from **₹10,052 cr** up,
  twenty times the ₹500 cr floor. Index membership is a far stricter size filter
  than the threshold, so even with a source wired the gate could never reject
  anything. The review treated this as a hole to plug; it isn't one.
- **ADV — ACTIVE but barely binding.** Median ADV across the cached universe is
  **₹83 cr**, minimum **₹3.3 cr**, and the ₹5 cr floor rejects **4 of 500**
  names (0.8%). The review's suspicion was right — it is filtering on
  principle. Kept anyway: 0.8% costs nothing and it guards against a genuinely
  thin name entering the index. Just don't imagine it protects your fills.

  *Correction (same day):* this originally said "at ₹2L capital a position is
  ₹5–15k, so even the thinnest constituent turns over ~250× a position daily",
  repeating the review's premise without checking it. Positions were actually
  ₹1.2–6.8 lakh (see the sizing entry above), making the real headroom ~120×,
  not ~250×. Still comfortably non-binding, but the original reasoning was
  lucky rather than right.
- **Earnings blackout — INERT**, confirmed from observation rather than
  assertion: the audit counts how many symbols returned any earnings date and
  reports `0/58`. Flips to ACTIVE by itself the day an Indian source is wired.
- **Promoter pledge — INERT, and the one real hole.** Unlike market cap it
  *would* reject real names if connected; index membership does not screen for
  pledging. No free reliable source exists (NSE publishes shareholding patterns
  as quarterly filings, not an API). Left inert and loudly reported rather than
  quietly default-passing.

Also fixed: `quality.py`'s docstring claimed missing fields default-pass "with a
warning" — no such warning was ever emitted. Per-symbol warnings would fire 500
times a run and be tuned out, so the docstring now points at the audit instead.

**A bug found in this work, worth recording.** The first version of the audit
tested `m.get("market_cap_cr") is not None` and cheerfully reported
"ACTIVE — 501/501 symbols carry a market cap" when not one of them did: the
workbook is Excel-backed, so an empty numeric cell reads back as `float('nan')`,
not `None`. Caught by previewing the report against the real universe rather
than trusting the tests. `_has_value()` now handles None, NaN and blank strings,
with a regression test.

### Close-in-range scored — 2026-09-12

`scoring.close_in_range_points`: the confirmation bar's close within its own
high-low range, 0 (at the low) → 0 pts, 1 (at the high) → 5 pts, clamped.
Computed in the pre-close rescore and in the backtest signal, which are the only
places with an actual breakout bar.

**Where the 5 points came from matters more than the component itself.** They
came out of `stage`, which was a constant 20 for every scored candidate — stage
is a hard gate, so passing it carries no information, and those were 20 points
of pure offset. Stage is now 15. The maximum stays 100, and the arithmetic was
chosen so **a strong close scores exactly what it did before**; only a weak
close gives ground. That is also why an *unknown* close-in-range earns the full
5 rather than zero or a midpoint: the morning scan has no breakout bar, so
anything else would shift every watchlist score for no informational reason.
Every pre-existing test passed unchanged after this, which is the check that the
claim holds.

### Chase penalty on extension past the pivot — 2026-09-12

New `scoring.extension_penalty`: free up to 2% above the level, ramping to a
full −10 at 8%, capped beyond. `ScoringFeatures.extension_pct` is `None` in the
morning scan (no entry yet — absence of data is not evidence of a chase) and
computed from the confirmation close in `_rescore`, which is the scan that
commits money. Entering below the level (the pullback path) is never penalised.
The composite is floored at 0, since 0 means "failed a hard gate".

**A scored penalty, not the hard ~5% gate Minervini's rule suggests** — same
reasoning as the regime gate: a rejected alert leaves no `alert_features` row,
so a hard cut destroys the evidence needed to locate the real cut point. The
penalty plus `min_score_to_alert` already behaves as a soft gate.

Knobs: `thresholds.extension_free_pct` / `extension_max_pct` /
`extension_max_penalty` (set the last to 0 to disable while still logging).
All three `composite_score` call sites now go through `scoring.scoring_params`
so they can't drift apart as knobs accumulate.

### Volume component widened — 2026-09-12

New `scoring.volume_points`, piecewise-linear with a knee at the confirmation
gate: **0× → 0, 1.5× → 7.5, 3.0× → 15**, capped beyond. Half weight at the gate
rather than zero because clearing it is itself evidence; capped at 3× because a
10× spike is usually news, not accumulation; continuous at the knee so a
candidate crossing 1.5× doesn't lurch in rank; still ranked below the gate so
the morning watchlist can order candidates that haven't broken out yet.
`thresholds.volume_saturation_ratio` (default 3.0) tunes the top end and is
threaded through all three `composite_score` call sites.

`test_rescore_reflects_the_confirmation_volume` — which deliberately pinned the
dead behaviour — now asserts the opposite, that a 3× breakout outranks a 1.5×
one by 7.5 points.

**The consequence to watch, stated plainly:** a breakout that only just clears
the gate now scores 7.5 lower than it used to, so the floor for a confirmed
alert dropped from 38 to 30.5 and `min_score_to_alert: 60` became materially
harder to clear. **Expect fewer alerts.** The threshold was left at 60 rather
than nudged down to compensate, because moving both at once would make the
change impossible to attribute — and because the right value is a question for
`alert_features` once the sample exists, not another prior.

Intended to be calibrated empirically, but the workbook's `alert_features` and
`setup_watchlist` sheets were both **empty** at the time of the change, so the
curve is reasoned rather than fitted. Re-check it against the realised
volume-ratio distribution once trades accumulate.

### Market-regime gate — 2026-09-12

New `filters/regime.py`. Three votes — NIFTY vs its 200DMA, % of the universe
above its own 50DMA, India VIX vs its 20-day average — each −1/0/+1, summed to
[−3, +3]. ≥+2 risk_on, ≤−2 risk_off. Breadth is counted in the morning scan's
existing RS pre-pass, so it costs nothing extra.

**It sizes down rather than switching off**, which was the important design
call: a hard gate would discard exactly the observations needed to tell whether
the gate is right. risk_off → 0.5× `risk_per_trade_pct`, all-three-negative →
0.25×, everything else → 1.0×. It never sizes *above* baseline — there is no
evidence risk-on days deserve more, and inventing upside turns a filter into a
leverage knob. Stop, targets and R:R are untouched: a half-size trade that stops
out is still −1R.

`regime`, `regime_score`, `breadth_pct`, `nifty_trend` and `risk_multiplier` are
new columns on both `setup_watchlist` and `alert_features`, and are bucketed by
`paper.stats`. So the thresholds above — which are priors, not findings — become
calibratable from realised `pnl_r`. `regime.enabled: false` keeps measuring
without acting.

Assessed once per morning (breadth needs the whole universe) and read back by
the pre-close scan off the watchlist row. A failed ^NSEI fetch or a
pre-existing row sizes at 1.0: a data gap is not evidence of a bad tape.

Live check on 2026-09-12: ^NSEI resolves (497 bars), close 23,398 vs a 200DMA of
24,550 falling 0.78% over 20 days → `nifty=-1`. VIX 12.3, neutral. So the index
vote is currently negative and breadth decides whether the scanner sizes down.

Not applied in the backtest — that needs point-in-time breadth across the
universe. Still listed under #11.

### Gap-through-stop fills — 2026-09-12

`tracker.settle_one_trade` now fills a stop at `min(stop_loss, open)` and notes
`stop_gap_open:<price>` when the gap bound, rather than assuming the stop price
was available. `backtest.simulate_trade` applies the identical rule — including
to the breakeven stop after target 1, where "you can't lose money on this trade
anymore" was quietly false through a gap. The target side is unchanged on
purpose: booking the target on a bar that opened above it understates the gain,
which is the conservative direction.

### Transaction costs — 2026-09-12

New `paper/costs.py` + a `costs:` block in `config.yaml`: brokerage (Angel One's
lower-of-0.1%/₹20), STT, exchange txn, SEBI turnover, stamp duty, GST, and a
0.05%/leg slippage haircut. ~0.25–0.35% of round-trip turnover.

`pnl_inr` and `pnl_r` are now both net — the R-multiple nets the *per-share*
cost off the move before dividing by risk per share, so rupees and R tell the
same story. `gross_pnl_inr` and `costs_inr` are new `paper_trades` columns, and
the digest prints net, gross, and costs-as-%-of-gross so the drag is visible
rather than silently baked in. `costs.enabled: false` restores the old numbers
for comparison.

Trades settled before this exists carry no `gross_pnl_inr`; the digest falls
back to net for them rather than reporting gross as zero.

**What this changes about the decision to go live:** the paper log is no longer
optimistic in the two directions that would most encourage going live too early.
Re-read `python -m breakout.output.digest` after the next settle — the avg-R
figure will drop, and that drop is the honest number, not a regression.

### Price-adjustment handling — 2026-09-12

**What the review got wrong, recorded so it isn't re-litigated:** the claim was
that unadjusted splits were corrupting every signal. False for this feed —
**yfinance back-adjusts splits and bonuses even with `auto_adjust=False`**
(verified: HEG's 5:1 split of 2024-10-18 shows no cliff in the raw series).
`auto_adjust=True` adds only *dividend* adjustment, ~1–2% on older bars.

What is genuinely broken is **demergers and capital reductions** — Yahoo has no
split record for them, so the drop sits in the series as if it were a day's
trading, and refetching cannot clear it. The live cache carried seven: VEDL
−65%, ABFRL −67%, HEG −63%, TMPV −40%, TRENT −33% (real actions) plus
INDUSINDBK −27% and IEX −30% (genuine crashes — the bands can't separate the
two, demerger ratios being arbitrary).

Shipped: `data/validate.py::is_continuous` hard-skips such series in all three
jobs including the morning RS pre-pass (one −65% phantom return drags every
other symbol's percentile); `Store.upsert_prices` re-bases cached bars *and the
levels on open paper trades* when a feed re-adjusts (without that, a split on a
held position reads as an instant ~−50% stop-out and destroys the paper record);
`scripts/refetch_prices.py` for the one-time repair.

Measured impact was small — all five affected names were already non-Stage-2, so
the stage filter was accidentally shielding us. The real harm was inflated
`base_height` giving an unreachable `target_2`, distorted ATR giving the wrong
stop and size, RS cross-section drag, and *suppressed* rather than false signals.

### Per-alert feature logging — 2026-09-12

New `alert_features` sheet, one row per alert with every predictor the scanner
saw, joined to the outcome by `trade_id`. `setup_watchlist` widened to carry the
morning's features as real columns. `paper.stats.feature_report` buckets each
feature against realised `pnl_r`. See GUIDE § 14.

Fixed in passing: `preclose_scan` no longer hardcodes
`rs_rank=0 / tightness=0 / sector=flat` into the alert (the CSV was misreporting
them), and the pre-close rescore calls the real `composite_score` over stored
features instead of an arithmetic patch that drifted whenever a weight changed.

**This is the prerequisite for Tier 2.** The weights in `scoring.py` are priors;
they become evidence-based only after a few dozen settled trades per bucket.
Until then `python -m breakout.paper.stats` will say the sample is thin, and it's
right to.

### Known limits of the score itself

Not bugs, but worth internalising (documented in GUIDE § 11):

- **Stage contributes no discrimination** — it's a hard gate, so every scored
  candidate gets exactly 20. It shifts all scores up by a constant, which means
  `min_score_to_alert: 60` is really "≈40 points of varying signal".
- **Ranking is still driven substantially by pattern confidence**, whose
  baselines are detector-specific (52w starts at 50, Darvas 60, NR7 50) — i.e.
  partly by *which* detector fired rather than how good the setup is. Volume now
  competes with it for influence, which it did not before #4 was fixed.
