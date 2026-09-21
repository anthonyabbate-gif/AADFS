# AADFS — weekly NFL consensus rankings (and a FanDuel cash lineup builder)

Two things live here. The main one is a **weekly consensus ranking board**: it
pulls as many independent inputs as it can reach, blends them into one ranking
per position, and shows you where they agree and where they split. The second is
a FanDuel **cash-game lineup builder** that the rankings feed into.

Rankings came second but matter more. They need no salary file, no FanDuel
account and no subscription to be useful, and the thing they tell you — *which
players are genuinely separated, and which are a coin-flip the sources cannot
agree on* — is the part that survives contact with a real Sunday.

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
.venv/bin/aadfs sources doctor   # which inputs can this machine reach?
.venv/bin/aadfs rankings         # build this week's consensus board
.venv/bin/aadfs serve            # or use the browser: http://127.0.0.1:8000
```

**Run `sources doctor` first.** It tries every input and tells you which
returned data, which need an API key, and which your network cannot reach. It is
the fastest way to find out what you are actually working with.

---

## Consensus rankings

### The inputs

| Source | Kind | Needs | Notes |
|---|---|---|---|
| Sleeper | projection | nothing | Weekly projections, re-scored under FanDuel rules. |
| ESPN | projection | nothing | Weekly projected stat lines. |
| NFL.com | projection | nothing | Their own fantasy projections. |
| Fantasy Nerds | ranking | API key | `TEST` works as a key while you evaluate it. |
| Vegas totals | market | free API key | Implied team totals from sportsbook lines. |
| Recent form | model | nothing | Computed here from nflverse results. Works offline. |
| Your CSVs | ranking | a file | Any export with a player column and a rank or projection. |

The mix is deliberate. Ten feeds reselling one model are worth less than three
that disagree for real reasons, so the roster spans fantasy projections, expert
orderings, betting markets and a model computed locally. The local model earns
its place by never failing: it needs no key, no subscription and nobody else's
server to be up on a Saturday.

Add your own with `--csv`, and they are weighted above the free feeds by default
— a source you chose to pay for should generally outrank one that costs nothing,
at least until the scoring below says otherwise.

### How the blend works

**Sources are combined on rank, not points.** One source's 14.2 and another's
11.8 may say exactly the same thing about a player if their scales differ; their
*orders* do not suffer that problem. It also lets a feed that only publishes an
order sit alongside one that publishes projections, which is the entire point of
casting a wide net.

**Thin coverage is penalised, not ignored.** A player rated by one source and
missed by six has not been "ranked 3rd" in any meaningful sense. Unrated players
are parked past the end of that source's list, so nobody floats to the top on a
single enthusiastic opinion.

**Disagreement is reported, not averaged away.** Eight sources putting a player
between 4th and 6th is a different claim from a player averaging 5th because
half say 1st and half say 9th. The board shows the spread, labels the agreement,
and flags the individual sources that are a long way from the rest.

**Tiers come from that disagreement.** A tier runs until a player is far enough
clear of the one who started it that their uncertainty bands stop overlapping,
so tiers are tight where sources agree and wide where they genuinely do not.
That is more useful than fixed groups of five.

### Working out which sources to trust

Adding a bad source to a consensus does not average out — it drags. So sources
can be graded against what actually happened:

```bash
aadfs rankings            # Saturday: capture what every source claims
aadfs sources score --save  # Tuesday: grade it, update the blend weights
```

Scoring reports Spearman correlation (did it get the order right?), mean
absolute rank error (how far off per player?) and top-12 hit rate (of the
players it called startable, how many were?). The last one matters most, because
the top of the board is where decisions get made.

Weights are centred on 1.0 and clamped between 0.3 and 2.0 on purpose. A source
that looked bad for three weeks might simply have had three bad weeks, and a
blend that swings hard on a small sample is worse than one that does not move.

**This only works going forward.** Live feeds publish the current week, so a
source cannot be graded on a past week after the fact — it has to be graded on
what it said at the time. That is why every board is written to disk when it is
built. A week you do not capture is accuracy data you cannot get back.

---

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

See **Hosting** below — there is a ready-made Docker setup in `deploy/`.

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

## Hosting

**Recommendation: a $5/month VPS, with the Docker setup in `deploy/`.**

The job is small — fetch a handful of APIs once a week, aggregate, store a few
hundred KB, serve a page — so the hardware barely matters. What matters is that
it is *running on Saturday*. The accuracy scoring depends on having captured
each week's board before the games, and a week missed is a week that cannot be
reconstructed afterwards. That single fact decides the hosting question:

- **A $5 VPS** (Hetzner, DigitalOcean, Vultr) stays up through your power cuts
  and ISP outages. This is what I would run.
- **A Raspberry Pi at home** costs nothing monthly and keeps your data in your
  house, which is a real advantage — but a blackout on a Saturday morning
  silently costs you a week of data collection.
- **Fly.io / Railway / Render** deploy easily, but their disks are ephemeral.
  You must attach a volume for `/data`, and an always-on instance costs about
  the same as a VPS anyway.

```bash
git clone <your fork> /srv/aadfs && cd /srv/aadfs/deploy
cp .env.example .env          # set AADFS_PASSWORD and any API keys
$EDITOR Caddyfile             # put your domain in
docker compose up -d
crontab -e                    # add the two lines from crontab.example
```

`crontab.example` captures the board on Saturday morning and grades it the
following Tuesday, once results are published.

If you would rather not expose anything publicly, drop the `caddy` service and
put the host on [Tailscale](https://tailscale.com) instead. Either way the app
refuses to bind to a non-local address until `AADFS_PASSWORD` is set.

**`AADFS_DATA` decides where everything is written** (boards, the cached
nflverse downloads, the results database). The compose file points it at the
mounted volume. Getting this wrong means writing inside the container, and
losing every saved board on the next redeploy.

I was not able to build the image here — this environment has the Docker client
but no daemon — so treat the first `docker compose build` as the real test. The
package itself installs cleanly from a fresh clone, which is most of what the
Dockerfile does.

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
- **Consensus is not accuracy.** Sources agreeing means they agree, not that
  they are right — they often read each other. Agreement narrows a tier; it does
  not guarantee the tier is in the right place.
- **The network adapters are written against documented response shapes but were
  never run against the live feeds**, because the environment they were built in
  could not reach them. `aadfs sources doctor` exists for exactly this: it tries
  every one and tells you which work. A feed that changes shape reports a clear
  error rather than returning silent nonsense, but expect to need a fix the
  first time you run it.

---

## Layout

```
aadfs/
  config.py         where files are written (AADFS_DATA)
  rankings/
    sources.py      every input: projections, rankings, markets, local model
    aggregate.py    rank blending, disagreement, tiers
    evaluate.py     grading sources against real results
    pipeline.py     building and saving the weekly board
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
deploy/             Docker Compose, Caddy and cron for a small host
```

Run the tests with `.venv/bin/python -m pytest`.

---

## Responsible use

DFS is gambling. This tool is for modelling your own entries; it does not
automate play, and a positive simulated ROI is a model output, not a promise.
Set a bankroll limit before the season, not during it.
