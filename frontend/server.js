import express from "express";
import fs from "fs";
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
