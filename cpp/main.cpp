#include <cmath>
#include <chrono>
#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <numbers>
#include <string>

[[gnu::always_inline]] inline double normal_cdf(double x) {
    const double a1 = 0.319381530;
    const double a2 = -0.356563782;
    const double a3 = 1.781477937;
    const double a4 = -1.821255978;
    const double a5 = 1.330274429;
    const double p = 0.2316419;
    const double t = 1.0 / (1.0 + p * std::abs(x));
    const double poly = ((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t;
    const double cdf = 1.0 - std::exp(-0.5 * x * x) * poly / std::sqrt(2.0 * std::numbers::pi);
    return x >= 0.0 ? cdf : 1.0 - cdf;
}

[[gnu::always_inline]] inline double black_scholes_call_put(double spot, double strike, double rate, double maturity, double vol, bool is_call) {
    const double sqrt_t = std::sqrt(maturity);
    const double d1 = (std::log(spot / strike) + (rate + 0.5 * vol * vol) * maturity) / (vol * sqrt_t);
    const double d2 = d1 - vol * sqrt_t;
    const double nd1 = normal_cdf(d1);
    const double nd2 = normal_cdf(d2);
    const double discount = std::exp(-rate * maturity);
    const double price = spot * nd1 - strike * discount * nd2;
    return is_call ? price : price - spot + strike * discount;
}

static double parse_double(const char* name, char** argv, int argc, double default_value) {
    for (int i = 1; i < argc - 1; ++i) {
        if (std::string(argv[i]) == name) {
            char* end = nullptr;
            errno = 0;
            const double value = std::strtod(argv[i + 1], &end);
            if (end == argv[i + 1] || *end != '\0' || errno == ERANGE) {
                return std::numeric_limits<double>::quiet_NaN();
            }
            return value;
        }
    }
    return default_value;
}

static std::string parse_string(const char* name, char** argv, int argc, const std::string& default_value) {
    for (int i = 1; i < argc - 1; ++i) {
        if (std::string(argv[i]) == name) {
            return argv[i + 1];
        }
    }
    return default_value;
}

int main(int argc, char** argv) {
    const double spot = parse_double("--spot", argv, argc, 100.0);
    const double strike = parse_double("--strike", argv, argc, 100.0);
    const double rate = parse_double("--rate", argv, argc, 0.05);
    const double maturity = parse_double("--maturity", argv, argc, 1.0);
    const double vol = parse_double("--vol", argv, argc, 0.2);
    const std::string type = parse_string("--type", argv, argc, "call");
    const double requested_iterations = parse_double("--iterations", argv, argc, 100000.0);

    if (!std::isfinite(spot) || !std::isfinite(strike) || !std::isfinite(rate) ||
        !std::isfinite(maturity) || !std::isfinite(vol) || spot <= 0.0 || strike <= 0.0 ||
        maturity <= 0.0 || maturity > 30.0 || vol <= 0.0 || vol > 10.0 || rate < 0.0 || rate > 1.0 ||
        !std::isfinite(requested_iterations) || requested_iterations <= 0.0 || requested_iterations > 1'000'000 ||
        requested_iterations > std::numeric_limits<long long>::max() ||
        std::floor(requested_iterations) != requested_iterations || (type != "call" && type != "put")) {
        std::cerr << "Invalid inputs: rate must be 0..1, maturity 0..30, volatility 0..10; "
                  << "iterations must be an integer in 1..1000000; type must be call or put.\n";
        return EXIT_FAILURE;
    }
    const long long iterations = static_cast<long long>(requested_iterations);

    const bool is_call = type == "call";
    const auto start = std::chrono::high_resolution_clock::now();
    double total = 0.0;
    for (long long i = 0; i < iterations; ++i) {
        total += black_scholes_call_put(spot, strike, rate, maturity, vol, is_call);
    }
    const auto end = std::chrono::high_resolution_clock::now();
    const auto price = black_scholes_call_put(spot, strike, rate, maturity, vol, is_call);
    const auto elapsed_ms = std::chrono::duration<double, std::milli>(end - start).count();

    std::cout << "price=" << price << "\n";
    std::cout << "benchmark_iterations=" << iterations << "\n";
    std::cout << "benchmark_total=" << total << "\n";
    std::cout << "elapsed_ms=" << elapsed_ms << "\n";
    return 0;
}
