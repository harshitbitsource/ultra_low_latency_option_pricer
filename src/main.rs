use std::env;

#[inline(always)]
fn normal_pdf(x: f64) -> f64 {
    (1.0 / (2.0 * std::f64::consts::PI).sqrt()) * (-0.5 * x * x).exp()
}

#[inline(always)]
fn normal_cdf(x: f64) -> f64 {
    let a1 = 0.319381530f64;
    let a2 = -0.356563782f64;
    let a3 = 1.781477937f64;
    let a4 = -1.821255978f64;
    let a5 = 1.330274429f64;
    let p = 0.2316419f64;
    let t = 1.0 / (1.0 + p * x.abs());
    let poly = (((((a5 * t + a4) * t + a3) * t + a2) * t + a1) * t) + 1.0;
    let cdf = 1.0 - normal_pdf(x) * poly;
    if x >= 0.0 { cdf } else { 1.0 - cdf }
}

#[derive(Debug, Clone, Copy)]
enum OptionType {
    Call,
    Put,
}

impl OptionType {
    fn from_str(value: &str) -> Self {
        if value.eq_ignore_ascii_case("put") {
            OptionType::Put
        } else {
            OptionType::Call
        }
    }
}

#[inline(always)]
fn black_scholes(spot: f64, strike: f64, rate: f64, maturity: f64, vol: f64, option_type: OptionType) -> (f64, f64, f64, f64, f64, f64) {
    let sqrt_t = maturity.sqrt();
    let d1 = (spot / strike).ln() + (rate + 0.5 * vol * vol) * maturity;
    let d1 = d1 / (vol * sqrt_t);
    let d2 = d1 - vol * sqrt_t;
    let nd1 = normal_cdf(d1);
    let nd2 = normal_cdf(d2);
    let pdf_d1 = normal_pdf(d1);
    let discount = (-rate * maturity).exp();

    let price = match option_type {
        OptionType::Call => spot * nd1 - strike * discount * nd2,
        OptionType::Put => strike * discount * (1.0 - nd2) - spot * (1.0 - nd1),
    };

    let delta = match option_type {
        OptionType::Call => nd1,
        OptionType::Put => nd1 - 1.0,
    };
    let gamma = pdf_d1 / (spot * vol * sqrt_t);
    let vega = spot * pdf_d1 * sqrt_t;
    let theta = match option_type {
        OptionType::Call => -spot * pdf_d1 * vol / (2.0 * sqrt_t) - rate * strike * discount * nd2,
        OptionType::Put => -spot * pdf_d1 * vol / (2.0 * sqrt_t) + rate * strike * discount * (1.0 - nd2),
    };
    let rho = match option_type {
        OptionType::Call => strike * maturity * discount * nd2,
        OptionType::Put => -strike * maturity * discount * (1.0 - nd2),
    };

    (price, delta, gamma, vega, theta, rho)
}

fn implied_volatility(spot: f64, strike: f64, rate: f64, maturity: f64, market_price: f64, option_type: OptionType) -> f64 {
    let tolerance = 1e-10;
    let max_iter = 100;
    let mut low = 1e-6;
    let mut high = 5.0;

    for _ in 0..max_iter {
        let mid = 0.5 * (low + high);
        let (mid_price, _, _, _, _, _) = black_scholes(spot, strike, rate, maturity, mid, option_type);
        if (mid_price - market_price).abs() < tolerance {
            return mid;
        }
        if mid_price > market_price {
            high = mid;
        } else {
            low = mid;
        }
    }

    0.5 * (low + high)
}

fn parse_arg(args: &[String], name: &str, default: f64) -> f64 {
    for i in 0..args.len() - 1 {
        if args[i] == name {
            return args[i + 1].parse::<f64>().unwrap_or(default);
        }
    }
    default
}

fn parse_string_arg(args: &[String], name: &str, default: &str) -> String {
    for i in 0..args.len() - 1 {
        if args[i] == name {
            return args[i + 1].clone();
        }
    }
    default.to_string()
}

fn has_flag(args: &[String], flag: &str) -> bool {
    args.iter().any(|value| value == flag)
}

fn print_usage() {
    println!("Ultra Low Latency Option Pricer");
    println!("Usage: ultra_low_latency_option_pricer [options]");
    println!("Options:");
    println!("  --spot <S>              Underlying spot price (default 100)");
    println!("  --strike <K>            Strike price (default 100)");
    println!("  --rate <r>              Risk-free rate as decimal (default 0.05)");
    println!("  --maturity <T>          Time to maturity in years (default 1)");
    println!("  --vol <sigma>           Volatility as decimal (default 0.2)");
    println!("  --type <call|put>       Option type (default call)");
    println!("  --iterations <N>        Benchmark iterations (default 100000)");
    println!("  --market-price <P>      Calculate implied volatility to match market price");
    println!("  --json                  Emit JSON output");
    println!("  --help, -h              Show this help message");
}

fn main() {
    let args: Vec<String> = env::args().collect();
    if has_flag(&args, "--help") || has_flag(&args, "-h") {
        print_usage();
        return;
    }

    let spot = parse_arg(&args, "--spot", 100.0);
    let strike = parse_arg(&args, "--strike", 100.0);
    let rate = parse_arg(&args, "--rate", 0.05);
    let maturity = parse_arg(&args, "--maturity", 1.0);
    let vol = parse_arg(&args, "--vol", 0.2);
    let option_type = OptionType::from_str(&parse_string_arg(&args, "--type", "call"));
    let iterations = parse_arg(&args, "--iterations", 100_000.0) as u64;
    let market_price = parse_arg(&args, "--market-price", f64::NAN);
    let json = has_flag(&args, "--json");

    let (price, delta, gamma, vega, theta, rho) = black_scholes(spot, strike, rate, maturity, vol, option_type);
    let implied = if market_price.is_finite() {
        Some(implied_volatility(spot, strike, rate, maturity, market_price, option_type))
    } else {
        None
    };

    let mut total = 0.0;
    let start = std::time::Instant::now();
    for _ in 0..iterations {
        total += black_scholes(spot, strike, rate, maturity, vol, option_type).0;
    }
    let elapsed = start.elapsed();
    let elapsed_ms = elapsed.as_secs_f64() * 1000.0;
    let avg_ns = if iterations > 0 { elapsed.as_nanos() as f64 / iterations as f64 } else { 0.0 };

    if json {
        let implied_string = implied.map_or("null".to_string(), |v| format!("{:.10}", v));
        println!(r#"{{"#);
        println!(r#"  "price": {:.10},"#, price);
        println!(r#"  "delta": {:.10},"#, delta);
        println!(r#"  "gamma": {:.10},"#, gamma);
        println!(r#"  "vega": {:.10},"#, vega);
        println!(r#"  "theta": {:.10},"#, theta);
        println!(r#"  "rho": {:.10},"#, rho);
        println!(r#"  "benchmark_iterations": {} ,"#, iterations);
        println!(r#"  "benchmark_total": {:.10},"#, total);
        println!(r#"  "elapsed_ms": {:.6},"#, elapsed_ms);
        println!(r#"  "avg_ns": {:.3},"#, avg_ns);
        println!(r#"  "implied_volatility": {}"#, implied_string);
        println!(r#"}}"#);
    } else {
        println!("price={:.10}", price);
        println!("delta={:.10}", delta);
        println!("gamma={:.10}", gamma);
        println!("vega={:.10}", vega);
        println!("theta={:.10}", theta);
        println!("rho={:.10}", rho);
        if let Some(implied_vol) = implied {
            println!("implied_volatility={:.10}", implied_vol);
        }
        println!("benchmark_iterations={}", iterations);
        println!("benchmark_total={:.10}", total);
        println!("elapsed_ms={:.6}", elapsed_ms);
        println!("avg_ns={:.3}", avg_ns);
    }
}
