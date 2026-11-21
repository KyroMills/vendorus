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

## Project layout

```
vendorus/        core Python package (settings, database, engines)
app/             Streamlit dashboard
tests/           automated tests
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
