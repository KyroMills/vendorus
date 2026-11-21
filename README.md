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

```bash
pytest                      # run the tests
streamlit run app/Home.py   # open the dashboard at http://localhost:8501
```

## Importing research data

Open the dashboard and go to the **Import** page. Pick products, keywords or
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

The **Profit** page shows what Amazon takes, what the product costs to land,
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

## Project layout

```
vendorus/        core Python package (settings, database, engines)
  importers/     data import: column matching, checks, CSV source
  fees/          Amazon fee tables (us.toml) and fee calculations
  profit.py      landed cost, profit, margins, break-even
app/             Streamlit dashboard (pages/ holds extra pages)
config/          planning assumptions (planning.toml)
templates/       blank CSV templates for hand-filled research
tests/           automated tests (fixtures/ holds made-up sample CSVs)
data/            local database and imports (never committed)
.env.example     template for local settings; copy to .env
PROGRESS.md      completed work and remaining tasks
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
