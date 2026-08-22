FROM debian:bookworm-slim AS cpp-builder
WORKDIR /usr/src/ultra_low_latency_option_pricer/cpp
COPY cpp/Makefile .
COPY cpp/main.cpp .
RUN apt-get update && apt-get install -y g++ make && make && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
RUN useradd --create-home --uid 10001 appuser
COPY --chown=appuser:appuser --from=cpp-builder /usr/src/ultra_low_latency_option_pricer/cpp/option_pricer ./cpp/option_pricer
COPY --chown=appuser:appuser app.py ./
COPY --chown=appuser:appuser frontend ./frontend
USER appuser
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
