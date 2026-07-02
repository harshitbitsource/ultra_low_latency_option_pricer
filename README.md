# Ultra Low Latency Option Pricer

This workspace contains a compact Black-Scholes option pricer in both Rust and C++.

## Build and run

### Rust

```bash
cd /home/harshit/Documents/ultra_low_latency_option_pricer
cargo run --release -- --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000
```

### C++

```bash
cd /home/harshit/Documents/ultra_low_latency_option_pricer/cpp
make
./option_pricer --spot 100 --strike 100 --rate 0.05 --maturity 1 --vol 0.2 --type call --iterations 100000
```

## Notes

- The implementation uses a numerically stable Black-Scholes closed-form formula.
- It is optimized for low latency with inline math and release-mode compilation.
- For real trading systems, you would typically extend this with SIMD, multithreading, and a proper benchmark harness.
