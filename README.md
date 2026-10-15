# Headline Impact

Pick a stock, then observe it through ten lenses: live price, every headline, how the market reacted,
sentiment, technicals, relative strength, volume, risk, and an AI analyst.

## Start (Windows)
1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
2. Copy `.env.example` to `.env` and fill in your four `AZURE_OPENAI_...` values.
3. Double-click `start.bat`. The first run installs everything; then your browser opens at http://127.0.0.1:8000.

Manual start: `pip install -r requirements.txt` then `python server.py`.

## The lenses
| Lens | What it shows |
|---|---|
| Overview | One tile per lens with a status signal, plus key findings |
| Live tape | Streaming price, intraday chart, session stats |
| Price and news | Candles with news markers; hover a day to read its headlines |
| News impact | Each headline's move (1st hour, day, 2 days, 1 week) vs the index, volume spike, verdict |
| Sentiment | Tone of coverage vs price, by news type and by source |
| Technicals | 20/50-day averages, RSI, MACD, range position |
| Relative strength | Stock vs Nifty 50 / Sensex / S&P 500 across windows |
| Volume and attention | Volume spikes, and spikes that had no headline |
| Risk | Volatility, beta, drawdown, VaR, return distribution |
| AI analyst / Ask AI | Written post-mortem and chat on the data |

## News sources
| Source | Type | Key needed |
|---|---|---|
| Google News, Bing News | News aggregators | No |
| Publisher RSS (ET, Moneycontrol, Mint, Business Standard, BusinessLine, Financial Express; CNBC, MarketWatch for US) | Publishers | No (edit `feeds.json`) |
| GDELT | Global online media, updated every 15 min | No |
| Yahoo Finance | Ticker news | No |
| NSE corporate announcements | Official filings | No |
| Reddit | Investor forums | Optional `REDDIT_CLIENT_ID` / `REDDIT_CLIENT_SECRET` |
| YouTube | Video | `YOUTUBE_API_KEY` |
| NewsAPI.org | 80,000+ news sites | `NEWSAPI_KEY` |
| Finnhub | US company news | `FINNHUB_KEY` |
| Custom RSS | Company press releases / IR | Add on the Keywords and sources lens |

Each stock has a keyword profile (company names, leadership, products, competitors, materials, exclusions, commodities),
built automatically and editable on the **Keywords and sources** lens, with **Suggest with AI**.
The feed refreshes every 2 minutes. Saved profiles live in `DATA_DIR` (default `./data`); on Render, add a persistent disk
and set `DATA_DIR` to its path, or profiles reset on each deploy.

## Where the data comes from
- **Prices:** Yahoo Finance via the `yfinance` library. The server polls it and streams updates to the browser
  (Server-Sent Events). Free data can lag the exchange by seconds to minutes.
- **News:** Yahoo Finance ticker news plus Google News RSS search on the company name.
- **Maths:** plain Python/pandas event study (no AI), so the numbers are exact.
- **AI:** Azure OpenAI (or Claude). Receives only headlines, measured reactions and a price summary.
  Keys stay in `.env` on the server and never reach the browser.

## Files
`server.py` web server and API, `data.py` prices and news, `analysis.py` event study and AI calls,
`lenses.py` lens calculations, `static/` the interface.

For research and learning, not investment advice.

## Deploy online
Start command on any host: `uvicorn server:app --host 0.0.0.0 --port 8000` (or use the Dockerfile).
Set the `AZURE_OPENAI_*` values plus `APP_USERNAME` and `APP_PASSWORD` as environment variables on the host.
Never upload the `.env` file. Run a single instance (the cache lives in memory).
