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

## Next up

- [ ] Review the fee table against Amazon and set `reviewed = true` in us.toml
- [ ] Check a few products in Amazon's Revenue Calculator (needs a Seller Central
      login)
- [ ] Not yet supported: apparel and dangerous-goods rate cards, oversize
      handling fees, storage for stock older than 180 days
- [ ] Phase 3: opportunity scoring (0-100, 10 factors, full breakdown)
- [ ] Phase 4: ranked opportunities dashboard (Milestone 1)

## Later

- [ ] Phase 5: validation (reviews, seasonality, risk flags)
- [ ] Phase 6: supplier tracking and landed costs
- [ ] Phase 7: Helium 10 MCP (Diamond plan) and Amazon SP-API
- [ ] Phase 8: operations module, cloud hosting

## Open decisions

- Helium 10 Diamond: planned, not yet purchased
- Amazon Professional seller account: status unknown
