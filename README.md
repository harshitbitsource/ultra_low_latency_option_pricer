# Ultra Low Latency Option Pricer

This workspace contains a compact Black-Scholes option pricer in both Rust and C++.

## Features

- Rust and C++ implementations of the Black-Scholes closed-form pricer.
- Benchmark harness with iteration-based timing.
- JSON output support for automated tooling.
- Greeks: delta, gamma, vega, theta, rho.
- Implied volatility calculation from a market price input.
- Docker image support for easy deployment.

## Build and run

### Rust

```bash
cd /home/harshit/Documents/ultra_low_latency_option_pricer
cargo run --release -- --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000
```

### Rust with JSON output

```bash
cargo run --release -- --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000 --json
```

### Rust implied volatility

```bash
cargo run --release -- --spot 100 --strike 100 --rate 0.05 --maturity 1 --type call --market-price 10.0
```

### C++

```bash
cd /home/harshit/Documents/ultra_low_latency_option_pricer/cpp
make
./option_pricer --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000
```

### Docker

Build the Docker image:

```bash
docker build -t ultra_low_latency_option_pricer .
```

Run the container:

```bash
docker run --rm ultra_low_latency_option_pricer --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000
```

## Deployment guidance

### Should you deploy it?

This repository is best suited as a low-latency pricing utility or benchmark component, not a full production trade engine.

It is worth deploying if you want:

- A standalone pricing service for research or latency measurement.
- A baseline implementation for comparison against optimized libraries.
- A small containerized tool for batch pricing and analytics.

It is not yet ready for direct trading use without adding:

- market data feeds and live order management
- error handling, logging, and observability
- vectorized/SIMD pricing and multi-threaded execution
- persistence, API gateway, and access control

### Deployment approaches

1. Local Docker run
   - Build and run the image locally for consistent results.
2. Kubernetes / container platform
   - Package it as a stateless service behind a small wrapper API.
   - Use the `--json` output to return machine-readable pricing and Greeks.
3. Benchmark-only environment
   - Use `--iterations` to stress test hardware and compare per-call latency.

## Notes

- The Rust implementation now exposes Greeks and implied volatility.
- The code is optimized for release build and inline math, but it is still single-threaded.
- For production use, add SIMD, threading, and a proper API layer.
