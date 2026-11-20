# Ultra Low Latency Option Pricer

Responsive local option-research dashboard with NSE equity quotes, model comparison, Greeks, volatility regimes, and strategy payoff analysis.

## Included

- Black–Scholes Greeks, American binomial tree, and antithetic Monte Carlo comparison
- Native C++ Black–Scholes price calculation through an in-process shared library
- Strike × DTE Greek sensitivity table and interactive spot/vol/rate inputs
- Realized/Garman–Klass volatility summary with IV-versus-realized regime flags
- Long/short call, long/short put, ATM straddle, and collar expiry P&L diagrams
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

Local runs build the native pricing executable and shared library at application
startup, so `g++` and `make` must be available. The Docker image builds and
ships both native artifacts.

Running `./.venv/bin/python app.py` directly binds to `127.0.0.1` (loopback) by
default. To serve on the local network on purpose, set `HOST=0.0.0.0` (and
optionally `PORT`). The Docker image already binds `0.0.0.0` for container use.

## Notes

- NSE equity quotes depend on NSEPython and are subject to NSE access restrictions.
- Crypto quotes use Binance via `ccxt`.
- Underlying price history is supplemental Yahoo Finance data when NSE does not
  provide bars. Historical bars and option prices may be unavailable.
- Estimated option prices and IV are model scenarios, not live option-chain data.
- The frontend is a minimal static page served from `frontend/index.html`.
