# Vendorus progress

## Completed

- [x] Phase 0: project foundation
  - Folder structure, README, `.gitignore`, `.env.example`
  - Settings loader (environment variables, `.env` support)
  - Database connection via SQLAlchemy (SQLite default)
  - Placeholder Streamlit dashboard with system status
  - Foundation tests

- [x] Phase 1: data import
  - Database tables: products, keywords, competitor ASINs, research snapshots
    (each import is a dated snapshot; re-importing adds history, never overwrites)
  - CSV importer with validation: bad rows are skipped with row number and reason
  - Flexible column names, so exports from different tools work without editing
  - Only ASIN (or keyword phrase) is required; blank optional fields are recorded
    per row so scoring can flag "not enough data"
  - Blank templates in `templates/`; Import page in the dashboard
  - Importer pipeline takes any source, so a Helium 10 MCP connector can plug in later
  - Helium 10 is not required: hand-collected data works, but monthly sales,
    revenue and search volume come only from paid tools

- [x] Phase 2: fee and profit engine (organic-first)
  - Amazon US fee tables in `vendorus/fees/us.toml`, copied from Amazon's pages
    on 2026-11-21 with source links: 2026 non-peak, 2026 holiday peak and 2027
    non-peak fulfillment fees, 2026 and 2027 size tiers, referral fees, storage,
    3.5% fuel and logistics surcharge
  - Checked against all six worked examples on Amazon's fee page (18 checks)
  - Landed cost, profit per unit, margin, return on landed cost, break-even price
  - Advertising off by default; optional per-product ad scenario shown side by
    side with the organic result. Returns at a configurable 5%
  - Every result states that organic ranking and sales are not guaranteed and
    lists the costs not included
  - Product import accepts size and weight, converting oz, kg, g, cm and mm
  - Costs saved per ASIN; re-imports never overwrite them
  - Older databases are upgraded in place (new columns added)

- [x] Dashboard: modern light and dark theme, sidebar navigation, Overview page
- [x] Desktop app: Vendorus opens in its own window (pywebview) with its own
  Dock icon on Mac and a desktop shortcut on Windows; closing it stops the
  dashboard (tested on macOS; Windows not yet tested on a Windows PC)

- [x] Phase 3: opportunity scoring
  - 0-100 score from 10 factors: market demand, search demand, review
    barrier, competition density, room to do better (ratings), sales spread,
    organic margin, return on cost, price point, fee efficiency
  - Limits, weights, bands and reasons in `config/scoring.toml`
  - Missing data is left out, never guessed; total scaled over scored
    factors; coverage always shown; under 6 of 10 = "Not enough data" (no
    headline score)
  - Hard stops: loses money with no ads = "Not viable"; warnings for fragile
    price, oversize, apparel/hazmat, research older than 90 days
  - Score page with a per-factor breakdown; hand-worked example tested

- [x] Phase 4: ranked opportunities (Milestone 1 complete)
  - Opportunities page: every product ranked by score, in three tabs
    (Ranked / Needs data / Not viable) so incomplete or money-losing
    products never sit in the ranking
  - Filters (search, band, category, price, minimum score, no warnings),
    remembered between pages
  - Click a row to open its Score page; Score and Profit pages open a
    product from the address (e.g. /score?asin=...) and link to each other
  - Compare 2-4 products factor by factor; CSV export with every factor
  - Overview shows the top 5 opportunities
  - All products scored with a few bulk queries and cached until data or
    settings change (500 products in about 0.3 s)
  - End-to-end test: import, costs, score, rank, export

- [x] Phase 5: validation
  - New imports: trends (monthly search volume or sales history) and
    customer reviews; competitors can carry a brand
  - Seasonality: pattern (steady / moderate / highly seasonal), peak months,
    and 12-month demand trend (needs 24 months)
  - Reviews: rating breakdown, complaint share, the phrases complaints
    mention most (with examples), review velocity from repeat imports
  - Risk flags: dominant brand or listing, declining demand, fast review
    growth, product types needing compliance checks (children's, supplements,
    topicals, batteries...), plus profit warnings; rules in
    `config/validation.toml`
  - Validate page with research history, a checklist and a go / watch /
    no-go decision per ASIN; Opportunities shows Risks, Season and Decision
  - Validation never changes the score
  - Chart colours switched to a palette validated for colour-blind
    separation and contrast in light and dark mode

## Milestone 1: done

Import real research data, calculate organic profitability from Amazon's
own fees, score every product 0-100 with a full breakdown, and rank them.

## Next up

- [ ] Review the fee table against Amazon and set `reviewed = true` in us.toml
- [ ] Check a few products in Amazon's Revenue Calculator (needs a Seller Central
      login)
- [ ] Not yet supported: apparel and dangerous-goods rate cards, oversize
      handling fees, storage for stock older than 180 days

## Later

- [ ] Phase 6: supplier tracking and landed costs
- [ ] Phase 7: Helium 10 MCP (Diamond plan) and Amazon SP-API
- [ ] Phase 8: operations module, cloud hosting

## Open decisions

- Helium 10 Diamond: planned, not yet purchased
- Amazon Professional seller account: status unknown
