# Vendorus

AI-assisted operating system for an Amazon FBA private-label business.
First milestone: a product research engine that imports real data, calculates
profitability, scores opportunities and shows ranked results.

## Requirements

- Python 3.12 or newer
- Git
- Works on macOS and Windows

## Setup

### macOS

```bash
git clone <your-repo-url> vendorus
cd vendorus
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### Windows (PowerShell)

```powershell
git clone <your-repo-url> vendorus
cd vendorus
py -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

If PowerShell blocks the activate script, run once:
`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`

## Run

### Desktop app (easiest)

Vendorus opens in its own app window, like any other program: no browser
tabs or address bar. Closing the window (or quitting) stops it.

- **Mac:** run `scripts/make_mac_app.sh` once. It builds `Vendorus.app` in
  your Applications folder (Home > Applications). Open it, then right-click
  its Dock icon and choose Options > Keep in Dock. The first time, macOS asks
  whether Vendorus may access your Documents folder (where this project
  lives); choose Allow. You can also double-click `Vendorus.command`.
- **Windows:** double-click `Vendorus.bat`. The first run installs what's
  needed and puts a **Vendorus** shortcut on your desktop; use that from
  then on. The window uses Microsoft Edge WebView2, which is built into
  Windows 10 and 11.

The app sets everything up on first run (creates `.venv` and installs the
requirements), picks a free port, and only listens on this computer, not
your network. Logs go to `data/launcher.log`. To use your browser instead,
run `python launcher.py --browser`.

If you move the project folder, run `scripts/make_mac_app.sh` again (Mac) or
delete the desktop shortcut and double-click `Vendorus.bat` (Windows).

### From a terminal

```bash
pytest                      # run the tests
streamlit run app/Home.py   # open the dashboard at http://localhost:8501
```

## How to use Vendorus

1. **Import data** (Research > Import data): products, then their
   competitors and keywords. Link competitors through "competes with" (the
   product's ASIN or keyword) and keywords through their product ASIN.
2. **Enter costs** (Profit): referral category, packaged size and weight,
   unit cost, freight, duty. Saved per ASIN.
3. **Check the score** (Score): see each factor and what data is missing.
4. **Rank** (Opportunities): every product ranked, with filters, a
   side-by-side comparison and a CSV export. Click a row to open its score.
5. **Validate** (Validate): import a trends file and reviews for the
   products you like, check the risk flags, seasonality and complaints,
   work through the checklist and record go / watch / no-go.
6. **Source** (Sourcing > Suppliers, Quotes): add suppliers, enter their
   quotes, compare landed cost, profit and score per quote, and use the
   best one for the product's costs.

## Importing research data

Open the dashboard and go to **Import data** in the sidebar. Pick products, keywords or
competitors, upload a CSV and click Import. You can use any tool's export or
fill in a blank template from `templates/` by hand.

- Only `asin` is required (`phrase` for keywords; `asin` and `competes_with`
  for competitors). Everything else is optional.
- Column names are matched loosely: `Price ($)`, `price` and `PRICE` all work.
  The page lists every accepted column name.
- Bad rows are skipped and listed with their row number and the reason. Good
  rows are still imported.
- Each import is saved as a dated snapshot, so importing again adds history
  instead of overwriting.

## Profit calculator

The **Profit** page (sidebar) shows what Amazon takes, what the product costs to land,
and what's left per unit. Pick an imported product (or enter one by hand),
confirm the referral fee category, and enter the packaged size and weight and
your costs. Costs are saved per ASIN.

- **Organic-first.** Advertising is off by default. Turn on the advertising
  scenario for a product to see profit with and without ads side by side.
- **Not a sales forecast.** Results show profit per unit *if* a unit sells.
  Organic ranking and sales are not guaranteed, especially for new listings.
- **Fees come from Amazon, not estimates.** `vendorus/fees/us.toml` holds the
  fee tables with their source pages and dates. When Amazon changes fees, add
  a new period there. The page warns when the table has expired.
- **Planning defaults** (returns 5%, months in storage, ad spend 0%) are in
  `config/planning.toml`. Each product can override them.
- The page lists the costs that are not included (inbound placement, peak
  fees unless selected, aged storage, promotions, the monthly plan fee and more).

## Opportunity score

The **Score** page gives each product a 0-100 score from ten factors (demand,
organic competition, organic profit and fees), showing the number behind
every point. A product is scored with its market: competitors whose
"competes with" is its ASIN or one of its keywords, and keywords linked to
its ASIN. Profit factors use the Profit page's numbers with no advertising.

- Missing data is left out, never guessed. Coverage is always shown, and
  products with fewer than 6 scored factors get no score until you add data.
- A product that loses money with no advertising is "Not viable".
- Limits, weights and score bands are in `config/scoring.toml`, with the
  reason for each. They're starting assumptions; tune them as you learn.

## Validation

The **Validate** page checks whether a promising product holds up. It never
changes the score.

- **Seasonality** needs a *trends* import: 12+ months of search volume for
  the main keyword (or sales for the ASIN), e.g. a research tool's history
  export or Google Trends. 24 months also shows whether demand is growing
  or declining.
- **Reviews** needs a *reviews* import (ASIN, stars, date, title, text) for
  the product and its competitors. Vendorus lists the phrases that come up
  most in 1-3 star reviews. Review velocity comes from importing the same
  competitors again at least 14 days later.
- **Risk flags** come from your data (dominant brand, declining demand...)
  and from product-type rules in `config/validation.toml` (children's
  products, supplements, batteries...). They are prompts to check, not
  legal advice.

## Suppliers and quotes

Enter each supplier's quote on the **Quotes** page. Vendorus works out the
landed cost per unit for the quantity you plan to order: product (at the
right price break), packaging, freight, duty, per-order fees and tooling.

- Product price, packaging, tooling and samples are in the quote's currency,
  converted with an exchange rate **you** enter. Freight and import fees are
  in US dollars.
- The Incoterm decides what's already in the price: freight for CFR, CIF and
  DAP; freight and duty for DDP.
- Duty rates change often, so enter your product's rate yourself (look up
  its tariff code at https://hts.usitc.gov/). Vendorus never guesses one.
- **Use this quote** copies the landed cost into the product's costs, so
  Profit, Score, Validate and Opportunities all use it.

## Project layout

```
vendorus/        core Python package (settings, database, engines)
  importers/     data import: column matching, checks, CSV source
  fees/          Amazon fee tables (us.toml) and fee calculations
  profit.py      landed cost, profit, margins, break-even
  scoring.py     opportunity score (0-100) and its breakdown
  ranking.py     ranking, filters and CSV export
  validation.py  seasonality, reviews, risk flags, decisions
  sourcing.py    suppliers, quotes, landed cost
app/             Streamlit dashboard: Home.py (entry point and navigation),
                 views/ (one file per page), ui.py (shared styling)
.streamlit/      dashboard theme (light and dark, fonts, colours)
config/          planning, scoring, validation and sourcing settings
templates/       blank CSV templates for hand-filled research
tests/           automated tests (fixtures/ holds made-up sample CSVs)
data/            local database and imports (never committed)
.env.example     template for local settings; copy to .env
PROGRESS.md      completed work and remaining tasks
launcher.py      desktop app: opens the dashboard in its own window
scripts/         make_mac_app.sh builds Vendorus.app; make_icon.py draws the icon
```

## Key decisions

- **SQLite first, PostgreSQL later.** SQLAlchemy is used for all database
  access, so moving to PostgreSQL means changing `DATABASE_URL` in `.env`.
- **Secrets in `.env` only.** `.env` is git-ignored; `.env.example` documents
  the settings without values.
- **Numbers come from calculations, not AI opinion.** Scoring and fee logic are
  plain, tested Python. Claude is used for research and explanations.
- **Business data stays local.** Everything under `data/` is git-ignored.

## Syncing between computers

Commit and push from one computer, pull on the other:

```bash
git pull          # before you start
git add -A && git commit -m "Describe the change" && git push   # when done
```

Your `.env` file and `data/` folder are not synced; set them up on each computer.
