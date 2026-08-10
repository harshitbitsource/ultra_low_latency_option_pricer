# Ultra Low Latency Option Pricer Frontend

## Setup

The frontend is a static page served by the FastAPI application; it does not
have an npm project or a separate development server. From the repository root:

```bash
python -m pip install -r requirements.txt
python -m uvicorn app:app --reload
```

Open `http://127.0.0.1:8000` in a browser.

## Usage

- Edit the market inputs in the left panel.
- Click `Run analytics` to update model prices, Greeks, volatility, payoff and
  scenario charts.
- The C++ benchmark engine is available independently through `/api/price`.
- Select a strategy to model its full position, including short call, short put,
  ATM straddle and collar structures.
