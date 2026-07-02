use std::env;

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
    let cdf = 1.0 - (1.0 / (2.0 * std::f64::consts::PI).sqrt()) * (-0.5 * x * x).exp() * poly;
    if x >= 0.0 { cdf } else { 1.0 - cdf }
}

#[inline(always)]
fn black_scholes_call_put(spot: f64, strike: f64, rate: f64, maturity: f64, vol: f64, is_call: bool) -> f64 {
    let sqrt_t = maturity.sqrt();
    let d1 = (spot / strike).ln() + (rate + 0.5 * vol * vol) * maturity;
    let d1 = d1 / (vol * sqrt_t);
    let d2 = d1 - vol * sqrt_t;
    let nd1 = normal_cdf(d1);
    let nd2 = normal_cdf(d2);
    let discount = (-rate * maturity).exp();
    let price = spot * nd1 - strike * discount * nd2;
    if is_call { price } else { price - spot + strike * discount }
}

fn parse_arg(args: &[String], name: &str, default: f64) -> f64 {
    for (i, value) in args.iter().enumerate() {
        if value == name {
            if let Some(next) = args.get(i + 1) {
                return next.parse::<f64>().unwrap_or(default);
            }
        }
    }
    default
}

fn parse_string_arg(args: &[String], name: &str, default: &str) -> String {
    for (i, value) in args.iter().enumerate() {
        if value == name {
            if let Some(next) = args.get(i + 1) {
                return next.clone();
            }
        }
    }
    default.to_string()
}

fn main() {
    let args: Vec<String> = env::args().collect();
    let spot = parse_arg(&args, "--spot", 100.0);
    let strike = parse_arg(&args, "--strike", 100.0);
    let rate = parse_arg(&args, "--rate", 0.05);
    let maturity = parse_arg(&args, "--maturity", 1.0);
    let vol = parse_arg(&args, "--vol", 0.2);
    let option_type = parse_string_arg(&args, "--type", "call").to_lowercase();
    let iterations = parse_arg(&args, "--iterations", 100_000.0) as u64;

    let is_call = option_type == "call";
    let mut total = 0.0;
    for _ in 0..iterations {
        total += black_scholes_call_put(spot, strike, rate, maturity, vol, is_call);
    }

    let price = black_scholes_call_put(spot, strike, rate, maturity, vol, is_call);
    println!("price={:.6}", price);
    println!("benchmark_iterations={}", iterations);
    println!("benchmark_total={:.6}", total);
}
