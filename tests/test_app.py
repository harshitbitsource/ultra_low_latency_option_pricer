import json
import unittest

from app import parse_yahoo_chart_payload


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
