# Ultra Low Latency Option Pricer

Responsive local option-research dashboard with NSE equity quotes, model comparison, Greeks, volatility regimes, and strategy payoff analysis.

## Included

- Black–Scholes Greeks, American binomial tree, and antithetic Monte Carlo comparison
- Strike × DTE Greek sensitivity table and interactive spot/vol/rate inputs
- Realized/Garman–Klass volatility summary with IV-versus-realized regime flags
- Long call, long put, straddle, and collar expiry P&L diagrams
- Responsive dark/light layout for laptop, tablet, and mobile
- Measured in-process analytics/request timing (not a misleading end-to-end latency claim)

## Data note

NSE equity quotes use `nsepython` with a Yahoo fallback. The dashboard explicitly marks market IV as an estimate unless a licensed/live option-chain provider is connected. Do not treat estimated IV or simulated prices as NSE option quotes.

## Install

cd /home/harshit/Documents/ultra_low_latency_option_pricer
./.venv/bin/python -m pip install -r requirements.txt

## Run

./.venv/bin/python -m uvicorn app:app --reload

Open `http://127.0.0.1:8000` in your browser.

## Notes

- NSE equity quotes depend on NSEPython and are subject to NSE access restrictions.
- Crypto quotes use Binance via `ccxt`.
- The frontend is a minimal static page served from `frontend/index.html`.
