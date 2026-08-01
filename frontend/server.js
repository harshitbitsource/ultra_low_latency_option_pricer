import express from "express";
import fs from "fs";
import https from "https";
import path from "path";
import { fileURLToPath } from "url";
import { spawnSync } from "child_process";

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);
const app = express();
const port = process.env.PORT ? Number(process.env.PORT) : 5174;
const repoRoot = path.resolve(__dirname, "..");
const cppDir = path.join(repoRoot, "cpp");
const binaryPath = path.join(cppDir, "option_pricer");

const nseAgent = new https.Agent({
  minVersion: "TLSv1.2",
  maxVersion: "TLSv1.2",
  rejectUnauthorized: false,
});

async function fetchNseQuote(symbol) {
  const url = `https://www1.nseindia.com/live_market/dynaContent/live_watch/get_quote/ajaxGetQuoteJSON.jsp?series=EQ&symbol=${encodeURIComponent(symbol)}`;
  return new Promise((resolve, reject) => {
    https.get(url, {
      agent: nseAgent,
      headers: {
        Referer: `https://www1.nseindia.com/live_market/dynaContent/live_watch/get_quote/GetQuote.jsp?symbol=${encodeURIComponent(symbol)}`,
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
      },
    }, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try {
          const json = JSON.parse(body);
          resolve(json);
        } catch (err) {
          reject(new Error(`Invalid stock quote response: ${err.message || err}`));
        }
      });
    }).on("error", reject);
  });
}

async function fetchMoneycontrolQuote(symbol) {
  const url = `https://priceapi.moneycontrol.com/pricefeed/nse/equitycash/${encodeURIComponent(symbol)}`;
  return new Promise((resolve, reject) => {
    https.get(url, {
      headers: {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36",
        Accept: "application/json",
      },
    }, (res) => {
      let body = "";
      res.on("data", (chunk) => { body += chunk; });
      res.on("end", () => {
        try {
          const json = JSON.parse(body);
          if (!json?.data) {
            return reject(new Error("Invalid Moneycontrol quote response"));
          }
          const data = json.data;
          resolve({
            symbol,
            lastPrice: data.pricecurrent ? Number(data.pricecurrent) : data.HP ? Number(data.HP) : null,
            openPrice: data.OPN ? Number(data.OPN) : null,
            highPrice: data["52H"] ? Number(data["52H"]) : null,
            lowPrice: data["52L"] ? Number(data["52L"]) : null,
            prevClose: data.priceprevclose ? Number(data.priceprevclose) : null,
            change: data.pricechange ? Number(data.pricechange) : null,
            raw: data,
          });
        } catch (err) {
          reject(new Error(`Invalid Moneycontrol quote response: ${err.message || err}`));
        }
      });
    }).on("error", reject);
  });
}

async function fetchStockQuote(symbol) {
  try {
    return await fetchNseQuote(symbol);
  } catch (error) {
    return await fetchMoneycontrolQuote(symbol);
  }
}

function buildBinary() {
  const result = spawnSync("make", [], { cwd: cppDir, stdio: "inherit" });
  if (result.status !== 0) {
    throw new Error("C++ build failed");
  }
}

function runPricer(params) {
  const args = [
    `--spot`, String(params.spot),
    `--strike`, String(params.strike),
    `--rate`, String(params.rate),
    `--maturity`, String(params.maturity),
    `--vol`, String(params.vol),
    `--type`, params.type,
    `--iterations`, String(params.iterations),
  ];
  const result = spawnSync(binaryPath, args, { cwd: cppDir, encoding: "utf8" });
  if (result.error) {
    throw result.error;
  }
  if (result.status !== 0) {
    throw new Error(result.stderr || "Engine returned non-zero exit code");
  }
  const output = result.stdout.trim().split(/\r?\n/);
  const response = {};
  for (const line of output) {
    const [key, value] = line.split("=");
    if (key && value !== undefined) {
      response[key.trim()] = Number(value.trim());
    }
  }
  return response;
}

app.use(express.json());

app.get("/api/price", (req, res) => {
  try {
    if (!pathExists(binaryPath)) {
      buildBinary();
    }
    const spot = Number(req.query.spot ?? 100);
    const strike = Number(req.query.strike ?? 100);
    const rate = Number(req.query.rate ?? 0.05);
    const maturity = Number(req.query.maturity ?? 1);
    const vol = Number(req.query.vol ?? 0.2);
    const type = String(req.query.type ?? "call");
    const iterations = Number(req.query.iterations ?? 100000);
    const result = runPricer({ spot, strike, rate, maturity, vol, type, iterations });
    res.json(result);
  } catch (error) {
    res.status(500).json({ error: error.message || String(error) });
  }
});

app.get("/api/stock", async (req, res) => {
  const symbol = String(req.query.symbol ?? "").trim().toUpperCase();
  if (!symbol) {
    return res.status(400).json({ error: "symbol query parameter is required" });
  }

  try {
    const quote = await fetchStockQuote(symbol);
    res.json({ symbol, quote });
  } catch (error) {
    res.status(500).json({ error: error.message || String(error) });
  }
});

function pathExists(pathToCheck) {
  try {
    return Boolean(pathToCheck && fs.existsSync(pathToCheck));
  } catch {
    return false;
  }
}

app.listen(port, () => {
  // eslint-disable-next-line no-console
  console.log(`API server running at http://127.0.0.1:${port}`);
});
