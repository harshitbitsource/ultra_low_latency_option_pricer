FROM rust:1.76-slim as builder

WORKDIR /usr/src/ultra_low_latency_option_pricer
COPY Cargo.toml Cargo.lock ./
COPY src ./src

RUN cargo build --release

FROM debian:bookworm-slim
COPY --from=builder /usr/src/ultra_low_latency_option_pricer/target/release/ultra_low_latency_option_pricer /usr/local/bin/ultra_low_latency_option_pricer
ENTRYPOINT ["ultra_low_latency_option_pricer"]
