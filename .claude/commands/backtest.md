Query the DB for all alerts fired in the last 30 days.

For each alert:
1. Fetch current price from DexScreener for the contract address
2. Compare to price at alert time (from DB)
3. Classify: true positive (>100% gain), partial (50-100%), false positive (<50%)

Display summary table:
- Total alerts
- True positive rate (>100% gain in 24h)
- False positive rate (<50% gain in 24h)
- Average lead time to 2x price
- Best and worst performing alerts
