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

## Next up

- [ ] Phase 2: fee and profit engine
  - Amazon referral and FBA fee tables in a dated config file, sourced from Amazon
  - Landed cost, contribution margin, break-even price
  - Verified against Amazon's revenue calculator
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
