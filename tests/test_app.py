import json
import unittest

from app import (
    build_dashboard_payload,
    build_strategy_payoff,
    build_strategy_position,
    build_volatility_summary,
    parse_yahoo_chart_payload,
    strategy_metrics,
)


class TestDashboardHelpers(unittest.TestCase):
    def test_build_volatility_summary(self):
        series = [
            {"close": 100.0, "high": 101.0, "low": 99.0},
            {"close": 102.0, "high": 104.0, "low": 100.0},
            {"close": 101.0, "high": 103.0, "low": 100.0},
        ]
        summary = build_volatility_summary(series, 0.25)
        self.assertGreater(summary["realizedVol"], 0.0)
        self.assertIn(summary["signal"], {"buy", "sell", "neutral"})

    def test_build_dashboard_payload(self):
        payload = build_dashboard_payload(100.0, 100.0, 0.05, 1.0, 0.2, "call", [])
        self.assertIn("modelPrice", payload)
        self.assertIn("marketPrice", payload)
        self.assertIn("volatility", payload)
        self.assertIn("impliedVol", payload)

    def test_strategy_payoffs_and_risk_have_correct_direction(self):
        long_call = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "long_call")
        short_call = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "short_call")
        long_put = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "long_put")
        short_put = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "short_put")

        self.assertAlmostEqual(long_call["greeks"]["delta"], -short_call["greeks"]["delta"])
        self.assertAlmostEqual(long_put["greeks"]["vega"], -short_put["greeks"]["vega"])
        self.assertEqual(strategy_metrics(short_call, 100.0, "short_call")["maxLoss"], "Unlimited")

    def test_straddle_loses_near_strike_and_gains_on_large_move(self):
        position = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "straddle")
        payoff = build_strategy_payoff(100.0, position, "straddle")
        at_strike = min(payoff, key=lambda point: abs(point["spot"] - 100.0))
        self.assertLess(at_strike["pnl"], 0.0)
        self.assertGreater(payoff[0]["pnl"], at_strike["pnl"])
        self.assertGreater(payoff[-1]["pnl"], at_strike["pnl"])

    def test_collar_has_bounded_expiry_payoff(self):
        position = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.2, "collar")
        payoff = build_strategy_payoff(100.0, position, "collar")
        metrics = strategy_metrics(position, 100.0, "collar")
        self.assertEqual(len(position["legs"]), 3)
        self.assertLessEqual(max(point["pnl"] for point in payoff), metrics["maxGain"] + 0.01)


class TestYahooChartParser(unittest.TestCase):
    def test_parse_yahoo_chart_payload(self):
        payload = {
            "chart": {
                "result": [
                    {
                        "meta": {
                            "currency": "INR",
                            "symbol": "TCS.NS",
                            "exchangeName": "NSI",
                            "fullExchangeName": "NSE",
                            "instrumentType": "EQUITY",
                            "regularMarketPrice": 2430.9,
                            "chartPreviousClose": 2431.8,
                            "previousClose": 2460,
                        },
                        "timestamp": [1785383100, 1785383400, 1785383700],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [2436.199951171875, 2475.800048828125, 2471.60009765625],
                                    "high": [2481.10009765625, 2480.5, 2476.5],
                                    "low": [2436.199951171875, 2472, 2470],
                                    "close": [2475.5, 2472, 2472.199951171875],
                                }
                            ]
                        },
                    }
                ],
                "error": None,
            }
        }

        quote = parse_yahoo_chart_payload(payload, "TCS.NS")

        self.assertEqual(quote["symbol"], "TCS.NS")
        self.assertEqual(quote["lastPrice"], 2430.9)
        self.assertEqual(quote["openPrice"], 2436.199951171875)
        self.assertEqual(quote["highPrice"], 2481.10009765625)
        self.assertEqual(quote["lowPrice"], 2436.199951171875)
        self.assertEqual(quote["prevClose"], 2460.0)
        self.assertAlmostEqual(quote["change"], -29.1, places=1)
        self.assertEqual(len(quote["series"]), 3)
        self.assertEqual(quote["series"][0], {"ts": 1785383100, "close": 2475.5})


if __name__ == "__main__":
    unittest.main()
