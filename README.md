# AADFS — FanDuel NFL cash-game lineup builder

A local web app for building weekly NFL DFS lineups on FanDuel, tuned for
**cash games** (50/50s and double-ups) rather than tournaments.

Cash games are a different problem from GPPs. You are not trying to win; you are
trying to finish in the top half more than ~55.6% of the time, because a 1.8x
payout needs a 55.6% win rate to break even. That makes **floor, not ceiling**,
the number that matters — so this tool models each player's whole score
distribution, simulates your lineup against a realistic field, and tells you the
one thing that actually decides whether to enter: *what is my win probability,
and does it clear break-even?*

---

## Quick start

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/aadfs serve          # then open http://127.0.0.1:8000
```

Then, each week:

1. On FanDuel, open the contest and click **Download Players List**. That CSV is
   the slate.
2. Upload it on the **Load slate** tab, along with any projection CSVs you
   already pay for.
3. Click **Fetch free projection sources** to blend in the open sources.
4. On **Build lineups**, pick the *Cash* preset and build.
5. Export the FanDuel upload CSV, or just read the lineup off the screen.

From the command line:

```bash
aadfs scan                              # Saturday: pull sources, write projections
aadfs build --lineups 3                 # build and print lineups
aadfs backtest data/salaries/wk10.csv --season 2024 --week 10
aadfs import-results ~/Downloads/entries.csv
```

---

## About FanDuel data — what is and is not possible

You asked whether this can pull historical winning lineups and live salaries
straight from FanDuel. Here is the honest answer, because it shaped the design.

**Salaries — yes, via the CSV they give you.** FanDuel has no public API.
Their salary data sits behind authenticated endpoints, and scraping those breaks
their terms of service and breaks in practice every time they change something.
The supported path is the **Download Players List** button on the contest entry
screen. This app parses that file exactly, and salaries are usually posted early
in the week, so a Saturday scan has them.

**Historical winning lineups — not reliably, and they matter less than you'd
think.** Contest results pages are behind login and there is no API for them.
What you *can* export is **your own entry history** (History → Export), which
this app imports and tracks. That turns out to be the more useful signal anyway:
in a 50/50 you never need to beat the winning lineup, you need to beat the cash
line. So the app tracks your real cash rate against the rate you need, and
separately reconstructs — from real results — what the *best available* lineup
would have been, so you can see how much of a gap was projections versus lineup
construction.

**Saturday web scan — yes.** `aadfs scan` pulls every configured source, blends
them, and writes the week's projections, a report of what each source returned,
and starting lineups. Schedule it with cron:

```cron
# Every Saturday at 9am
0 9 * * 6 cd /path/to/AADFS && .venv/bin/aadfs scan >> data/scan.log 2>&1
```

### Where projections come from

| Source | What it gives | Notes |
|---|---|---|
| Your own CSVs | Whatever you subscribe to | Any CSV with a name and a projection column. **This should be your primary source.** |
| [Sleeper](https://sleeper.com) | Weekly projections as raw stats | Free, no key. Stats are re-scored under FanDuel rules. |
| ESPN | Weekly projected stat lines | Free, no key. Re-scored the same way. |
| FanDuel's own FPPG | Season average, from the salary file | Always present, low weight, used as a fallback. |
| [nflverse](https://github.com/nflverse/nflverse-data) | Real historical results | Open data. Used for variance, backtesting and D/ST scoring. |

Sources that give **raw stats** are preferred over ones that give a points
total, because scoring them ourselves under FanDuel's rules removes any
disagreement about scoring settings.

**A dead source never breaks a scan.** Every fetch failure is caught, recorded
in the report, and the blend proceeds on whatever arrived.

---

## Running it from an iPad

An iPad cannot run the app itself — the solver is a compiled binary and the data
stack has no iOS builds. What it can do is *use* the app, since the interface is
already a web page. So: run it on a machine that stays on, and open it in Safari.

The layout adapts to both orientations, touch targets are sized for fingers, and
inputs are set at 16px so Safari does not zoom the page every time you tap one.
Uploading the FanDuel CSV works through the Files app.

### The private way (recommended)

Run it on your desktop or a Raspberry Pi, and join both devices to a
[Tailscale](https://tailscale.com) network. Tailscale is free for personal use
and gives your machine a stable private address that only your own devices can
reach, encrypted end to end. Nothing is exposed to the internet.

```bash
export AADFS_PASSWORD='something-long-and-random'
aadfs serve --host 0.0.0.0
```

Then on the iPad, open `http://<your-tailscale-name>:8000` and enter the
password once. Safari remembers it.

### On a small cloud host

Any $5/month VPS works. Put it behind a reverse proxy with HTTPS (Caddy does
this in about three lines), set `AADFS_PASSWORD`, and run the same command. If
the host has ephemeral storage, mount a volume for `aadfs.db` and `data/` or you
will lose your history on every redeploy.

### About the password

The app is unprotected on localhost, because only you can reach it there. The
moment you bind to any other address, **`aadfs serve` refuses to start until
`AADFS_PASSWORD` is set** — it holds your lineups and contest history, and an
open port on a coffee-shop network is an open door. Set `AADFS_USERNAME` too if
you want something other than `aadfs`.

Basic auth encodes the password reversibly rather than encrypting it, so it is
only private over a connection that is itself encrypted. Tailscale and HTTPS
both qualify; plain HTTP over the open internet does not. Do not port-forward
this to the world.

---

## How the model works

### 1. Blending

Each source's number is matched onto the salary file and blended by weight. Name
matching is conservative: accents, punctuation and suffixes are normalised
(`Ja'Marr`/`JaMarr`, `Marvin Harrison Jr.`/`Marvin Harrison`), and genuinely
ambiguous names are **reported as unmatched rather than guessed**. Every scan
tells you what it could not match.

### 2. Each player is a distribution, not a number

A projection alone cannot tell you whether a player is safe. Each player gets a
standard deviation built from two things:

- **how much the sources disagree** about them this week, and
- **how much that player's scoring has actually bounced around**, measured from
  real nflverse results (falling back to position-level priors, themselves
  calibrated from real data, for players without enough history).

A player everyone agrees on with a steady history gets a tight distribution and
is a natural cash play. A player the sources split on has to clear a higher bar.

### 3. The downside is modelled explicitly

Scores are drawn from a **bust mixture**: with some probability a player has a
week that falls apart (hurt early, two targets in a blowout, a backfield split
that vanishes by halftime), otherwise a lognormal centred so the mixture still
matches the projection and spread.

This is not decoration. Backtesting against real 2024 results showed a plain
lognormal put **28.8% of actual outcomes below its own 20th-percentile floor** —
its left tail was far too thin, which makes risky lineups look safe. That is the
single worst error a cash tool can make. The mixture corrects most of the gap
and calibrates the upside almost exactly (15.3% above the 85th-percentile
ceiling, against a 15% target).

### 4. Correlation

Lineups are simulated through a Gaussian copula, so players move together the
way football actually works: a QB with his own receivers (+0.35), receivers
competing for the same targets (−0.10), a D/ST against the offense it faces
(−0.30), a shootout lifting both sides of a game (+0.15). **Busts are correlated
too** — when a quarterback's day collapses, his receivers' days collapse with
it, and modelling those independently would understate exactly the joint
downside cash lineups exist to avoid.

### 5. The verdict

Your lineup and a simulated field of opponents are scored on the *same* draws.
The cash line is read off the field in each simulation, and your win probability
is how often you clear it. That gives an expected ROI, compared against the
break-even rate your contest's payout implies.

### 6. Roster rules

FanDuel NFL Classic: QB / RB / RB / WR / WR / WR / TE / FLEX / D-ST, $60,000
cap, max 4 players per team, at least 3 games represented. Cash safety rails are
on by default: never a D/ST against your own players, never a QB against your
own D/ST, out players excluded. If your constraints make a lineup impossible,
the solver **relaxes them in a defined order and tells you exactly what it gave
up** rather than returning nothing.

---

## Limitations — read these

- **Projection quality dominates everything else.** The optimiser and simulator
  are only arranging the numbers you feed them. A naive season-average
  projection backtested at **4.19 points mean absolute error per player**; that
  is the bar a paid source needs to beat, and beating it is worth far more than
  any setting in this app.
- **The simulated win probability is optimistic in absolute terms.** The field is
  built from the same projections your lineup is optimised on, so your lineup
  gets a structural edge that will not exist in a real contest. Use the number
  to *compare lineups against each other*, not as a literal forecast of your
  cash rate. Your imported entry history is the honest measure.
- **Correlation and bust rates are reasoned estimates, not fitted parameters.**
  They are deliberately modest; overstating correlation makes stacks look safer
  than they are.
- **Roster rules can change between seasons.** `max_per_team` and `min_games` are
  configurable in `aadfs/scoring.py` for that reason. Check them against the
  contest's own rules page before trusting a lineup to be enterable.
- **D/ST scoring is reconstructed**, since nflverse publishes no team-defense
  fantasy totals. Points allowed is derived from the opposing team's touchdowns,
  field goals and extra points; that reconstruction reproduces real final scores
  exactly on every game checked.
- **This does not place bets.** It builds a CSV you upload yourself.

---

## Layout

```
aadfs/
  scoring.py        FanDuel scoring rules and roster construction
  models.py         Player, Slate, Lineup
  names.py          name/team normalisation and matching
  distribution.py   the bust-mixture score model
  consensus.py      blending sources into projection + floor + ceiling
  optimizer.py      MILP lineup optimisation with constraint relaxation
  simulate.py       correlated Monte Carlo against a simulated field
  pipeline.py       the flow the web app and CLI share
  tracker.py        entry-history import and hindsight backtesting
  store.py          local SQLite
  ingest/fanduel.py FanDuel CSV in, FanDuel CSV out
  sources/          Sleeper, ESPN, nflverse, your own CSVs
  jobs/             the Saturday scan
  web/              the local app
```

Run the tests with `.venv/bin/python -m pytest`.

---

## Responsible use

DFS is gambling. This tool is for modelling your own entries; it does not
automate play, and a positive simulated ROI is a model output, not a promise.
Set a bankroll limit before the season, not during it.
