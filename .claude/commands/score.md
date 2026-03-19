Score a specific token by contract address: $ARGUMENTS

Steps:
1. Fetch live data from DexScreener for the given contract address
2. Run the token through scorer.py to get the quantitative score
3. If score >= MIN_SCORE, run MiroFish simulation (if available) or Claude fallback
4. Display full breakdown: all 5 signal values, which fired, quant score, narrative score (if run), and projected conviction score
