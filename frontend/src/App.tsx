import { useMemo, useState } from "react";
import {
  AreaChart,
  Area,
  CartesianGrid,
  Tooltip,
  XAxis,
  YAxis,
  ResponsiveContainer,
} from "recharts";

function normalCdf(x: number) {
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422804014327 * Math.exp(-0.5 * x * x);
  const prob = d * t * (0.319381530 + t * (-0.356563782 + t * (1.781477937 + t * (-1.821255978 + 1.330274429 * t))));
  return x >= 0 ? 1 - prob : prob;
}

function buildSeries(
  S: number,
  K: number,
  r: number,
  sigma: number,
  t: number,
  steps: number
) {
  const series = [];
  for (let i = 0; i <= steps; i += 1) {
    const term = t * (i / steps);
    const price = blackScholesPrice(S, K, r, sigma, term);
    series.push({ t: Number(term.toFixed(3)), price: Number(price.toFixed(4)) });
  }
  return series;
}

function blackScholesPrice(S: number, K: number, r: number, sigma: number, t: number) {
  if (t <= 0) return Math.max(0, S - K);
  const sqrtT = Math.sqrt(t);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma * sigma) * t) / (sigma * sqrtT);
  const d2 = d1 - sigma * sqrtT;
  return S * normalCdf(d1) - K * Math.exp(-r * t) * normalCdf(d2);
}

export default function App() {
  const [spot, setSpot] = useState(100);
  const [strike, setStrike] = useState(100);
  const [rate, setRate] = useState(0.05);
  const [vol, setVol] = useState(0.25);
  const [time, setTime] = useState(1);
  const [steps, setSteps] = useState(30);
  const [iterations, setIterations] = useState(100000);
  const [price, setPrice] = useState<number | null>(null);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [stockSymbol, setStockSymbol] = useState("");
  const [stockQuote, setStockQuote] = useState<any>(null);
  const [stockError, setStockError] = useState<string | null>(null);
  const [stockLoading, setStockLoading] = useState(false);

  const data = useMemo(
    () => buildSeries(spot, strike, rate, vol, time, steps),
    [spot, strike, rate, vol, time, steps]
  );

  async function runPricing() {
    setLoading(true);
    setError(null);
    try {
      const params = new URLSearchParams({
        spot: String(spot),
        strike: String(strike),
        rate: String(rate),
        maturity: String(time),
        vol: String(vol),
        type: "call",
        iterations: String(iterations),
      });
      const response = await fetch(`/api/price?${params.toString()}`);
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || "Pricing engine failed");
      }
      setPrice(result.price ?? null);
      setElapsed(result.elapsed_ms ?? null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setPrice(null);
      setElapsed(null);
    } finally {
      setLoading(false);
    }
  }

  async function lookupStock() {
    if (!stockSymbol.trim()) {
      setStockError("Enter a stock symbol to search.");
      return;
    }
    setStockLoading(true);
    setStockError(null);
    try {
      const response = await fetch(`/api/stock?symbol=${encodeURIComponent(stockSymbol.trim().toUpperCase())}`);
      const result = await response.json();
      if (!response.ok) {
        throw new Error(result.error || "Stock lookup failed");
      }
      setStockQuote(result.quote);
    } catch (err) {
      setStockError(err instanceof Error ? err.message : String(err));
      setStockQuote(null);
    } finally {
      setStockLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="hero">
        <div>
          <p className="eyebrow">Ultra Low Latency Option Pricer</p>
          <h1>Interactive pricing + chart runner</h1>
          <p>
            Click the runner to build and execute the C++ engine, then view the computed price and timing.
          </p>
        </div>
        <button className="run-button" onClick={runPricing} disabled={loading}>
          {loading ? "Running..." : "Run Pricing"}
        </button>
      </header>

      <main className="content-grid">
        <section className="panel">
          <h2>Market Inputs</h2>
          <label>
            Spot Price
            <input
              type="number"
              value={spot}
              onChange={(e) => setSpot(Number(e.target.value))}
              step="1"
              min="1"
            />
          </label>
          <label>
            Strike Price
            <input
              type="number"
              value={strike}
              onChange={(e) => setStrike(Number(e.target.value))}
              step="1"
              min="1"
            />
          </label>
          <label>
            Interest Rate
            <input
              type="number"
              value={rate}
              onChange={(e) => setRate(Number(e.target.value))}
              step="0.01"
              min="0"
              max="1"
            />
          </label>
          <label>
            Volatility
            <input
              type="number"
              value={vol}
              onChange={(e) => setVol(Number(e.target.value))}
              step="0.01"
              min="0.01"
              max="2"
            />
          </label>
          <label>
            Time to Expiry (years)
            <input
              type="number"
              value={time}
              onChange={(e) => setTime(Number(e.target.value))}
              step="0.1"
              min="0.1"
              max="5"
            />
          </label>
          <label>
            Benchmark Iterations
            <input
              type="number"
              value={iterations}
              onChange={(e) => setIterations(Number(e.target.value))}
              step="1000"
              min="1000"
              max="10000000"
            />
          </label>
          <div className="stock-search-box">
            <h2>Live NSE Search</h2>
            <label>
              Stock symbol
              <input
                type="text"
                value={stockSymbol}
                onChange={(e) => setStockSymbol(e.target.value)}
                placeholder="e.g. TCS"
              />
            </label>
            <button className="run-button" onClick={lookupStock} disabled={stockLoading}>
              {stockLoading ? "Searching..." : "Search Stock"}
            </button>
            {stockError ? <p className="error">{stockError}</p> : null}
            {stockQuote ? (
              <div className="quote-card">
                <h3>{stockQuote.symbol || stockSymbol.toUpperCase()}</h3>
                <p>Last Price: {stockQuote.lastPrice !== undefined && stockQuote.lastPrice !== null ? stockQuote.lastPrice : "N/A"}</p>
                <p>Open Price: {stockQuote.openPrice !== undefined && stockQuote.openPrice !== null ? stockQuote.openPrice : "N/A"}</p>
                <p>High Price: {stockQuote.highPrice !== undefined && stockQuote.highPrice !== null ? stockQuote.highPrice : "N/A"}</p>
                <p>Low Price: {stockQuote.lowPrice !== undefined && stockQuote.lowPrice !== null ? stockQuote.lowPrice : "N/A"}</p>
              </div>
            ) : null}
          </div>
        </section>

        <section className="panel results-panel">
          <div className="result-card">
            <p>Computed Call Price</p>
            <strong>{price !== null ? `$${price.toFixed(4)}` : "—"}</strong>
            <p className="subtext">Engine elapsed time: {elapsed !== null ? `${elapsed.toFixed(2)} ms` : "—"}</p>
            {error ? <p className="error">{error}</p> : null}
          </div>
          <div className="chart-card">
            <h2>Price vs Time</h2>
            <ResponsiveContainer width="100%" height={320}>
              <AreaChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
                <defs>
                  <linearGradient id="colorPrice" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor="#4f46e5" stopOpacity={0.8} />
                    <stop offset="95%" stopColor="#4f46e5" stopOpacity={0.1} />
                  </linearGradient>
                </defs>
                <XAxis dataKey="t" tick={{ fill: "#94a3b8" }} />
                <YAxis tick={{ fill: "#94a3b8" }} domain={['auto', 'auto']} />
                <CartesianGrid strokeDasharray="3 3" stroke="#334155" />
                <Tooltip formatter={(value: number) => [`$${value.toFixed(4)}`, "Price"]} />
                <Area type="monotone" dataKey="price" stroke="#6366f1" fill="url(#colorPrice)" strokeWidth={2} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      </main>
    </div>
  );
}
