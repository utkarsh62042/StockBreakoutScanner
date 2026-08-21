from breakout.data.fetcher import YFinanceFetcher
from breakout.analysis.pivots import find_pivots
df = YFinanceFetcher().fetch_history("RELIANCE", days=300)
pivots = find_pivots(df, n=5)
for p in pivots.highs[-5:]:
    print(df.index[p.index].date(), p.price, "strength=", p.strength)

