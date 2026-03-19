Run one full pipeline cycle in dry-run mode (no alerts fired).

Show: tokens fetched from each source, candidates after dedup, scores for all tokens above 40 points, how many would have passed Stage 3.

Command: `uv run python -m scout.main --dry-run --cycles 1`

After running, report:
1. How many tokens were fetched from DexScreener vs GeckoTerminal
2. How many unique candidates after dedup
3. Score breakdown for each candidate above 40 points
4. How many would have triggered MiroFish (score >= MIN_SCORE)
