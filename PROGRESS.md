# Vendorus progress

## Completed

- [x] Phase 0: project foundation
  - Folder structure, README, `.gitignore`, `.env.example`
  - Settings loader (environment variables, `.env` support)
  - Database connection via SQLAlchemy (SQLite default)
  - Placeholder Streamlit dashboard with system status
  - Foundation tests

## Next up

- [ ] Phase 1: data import
  - Database tables: products, keywords, competitor ASINs, research snapshots
  - CSV importer with validation (rejects bad rows, reports why)
  - Importer designed so a Helium 10 MCP connector can plug in later
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
