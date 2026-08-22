import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app import (
    MAX_PRICER_ITERATIONS,
    AnalyticsRequest,
    SingleFlightTTLCache,
    api_analytics,
    api_stock,
    black_scholes_price_and_greeks,
    build_dashboard_payload,
    build_strategy_payoff,
    build_strategy_position,
    build_volatility_summary,
    calculate_gk_vol,
    health_check,
    is_rate_limited,
    parse_yahoo_chart_payload,
    provider_call,
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

    def test_black_scholes_rejects_unknown_option_type(self):
        with self.assertRaises(ValueError):
            black_scholes_price_and_greeks(100.0, 100.0, 0.05, 1.0, 0.2, "invalid")

    def test_analytics_request_rejects_invalid_strategy_and_inputs(self):
        with self.assertRaises(ValidationError):
            AnalyticsRequest(spot=0, strike=100, strategy="long_call")
        with self.assertRaises(ValidationError):
            AnalyticsRequest(spot=100, strike=100, strategy="unknown")

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

    def test_analytics_returns_repriceable_legs_for_every_strategy(self):
        for strategy in ("long_call", "long_put", "short_call", "short_put", "straddle", "collar"):
            with self.subTest(strategy=strategy):
                response = api_analytics(AnalyticsRequest(
                    spot=100, strike=100, rate=0.05, maturity=1, vol=0.25, strategy=strategy,
                ))
                self.assertEqual(len(response["payoff"]), 41)
                self.assertTrue(response["strategyLegs"])
                self.assertIn("positionCost", response)
                self.assertEqual(response["pricingInputs"]["spot"], 100)
                self.assertTrue(all("kind" in leg and "quantity" in leg for leg in response["strategyLegs"]))

    def test_strategy_metrics_match_expiry_payoff_at_boundaries(self):
        position = build_strategy_position(100.0, 100.0, 0.05, 1.0, 0.25, "collar")
        payoff = build_strategy_payoff(100.0, position, "collar")
        metrics = strategy_metrics(position, 100.0, "collar")
        self.assertAlmostEqual(min(point["pnl"] for point in payoff), -metrics["maxLoss"], places=3)
        self.assertAlmostEqual(max(point["pnl"] for point in payoff), metrics["maxGain"], places=3)

    def test_request_bounds_reject_extreme_model_inputs(self):
        for values in ({"rate": 1.1}, {"maturity": 31}, {"vol": 10.1}):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    AnalyticsRequest(spot=100, strike=100, **values)

    def test_garman_klass_requires_valid_ohlc_data(self):
        self.assertEqual(calculate_gk_vol([{"close": 100}, {"close": None}]), 0.0)
        vol = calculate_gk_vol([
            {"ts": 0, "close": 100, "high": 101, "low": 99},
            {"ts": 300, "close": 101, "high": 102, "low": 100},
        ])
        self.assertGreater(vol, 0.0)


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
        self.assertEqual(quote["series"][0], {
            "ts": 1785383100, "close": 2475.5,
            "open": 2436.199951171875, "high": 2481.10009765625, "low": 2436.199951171875,
        })

    def test_parse_yahoo_chart_ignores_non_numeric_highs_and_lows(self):
        payload = {
            "chart": {"result": [{"meta": {}, "timestamp": [], "indicators": {"quote": [{
                "open": [], "high": [None, "not-a-number", 102], "low": ["bad", 98], "close": [],
            }]}}]}
        }
        quote = parse_yahoo_chart_payload(payload, "TEST.NS")
        self.assertEqual(quote["highPrice"], 102.0)
        self.assertEqual(quote["lowPrice"], 98.0)


class TestApiSafety(unittest.TestCase):
    def test_native_pricer_limit_is_consistent_with_public_configuration(self):
        self.assertEqual(MAX_PRICER_ITERATIONS, 1_000_000)

    def test_health_check_is_dependency_free(self):
        self.assertEqual(health_check(), {"status": "ok", "service": "quantsight"})

    def test_provider_call_retries_transient_failure(self):
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) == 1:
                raise RuntimeError("temporary")
            return "ok"

        with patch("app.time.sleep"):
            self.assertEqual(provider_call("test", operation), "ok")
        self.assertEqual(len(attempts), 2)

    def test_ttl_cache_avoids_duplicate_loader_calls(self):
        cache = SingleFlightTTLCache()
        calls = []

        def load():
            calls.append(1)
            return {"value": 1}

        self.assertEqual(cache.get_or_load("key", 60, load), {"value": 1})
        self.assertEqual(cache.get_or_load("key", 60, load), {"value": 1})
        self.assertEqual(len(calls), 1)

    def test_rate_limit_returns_429(self):
        import app as application

        application._request_buckets.clear()
        with patch("app.RATE_LIMIT_REQUESTS", 1):
            self.assertFalse(is_rate_limited("127.0.0.1"))
            self.assertTrue(is_rate_limited("127.0.0.1"))

    @patch("app.fetch_nse_stock_quote")
    def test_stock_endpoint_does_not_expose_upstream_payload(self, fetch_quote):
        fetch_quote.return_value = {"symbol": "TCS", "lastPrice": 100.0, "raw": {"secret": "provider-data"}}
        response = api_stock(symbol="TCS")
        self.assertNotIn("raw", response["quote"])


if __name__ == "__main__":
    unittest.main()
