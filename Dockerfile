FROM node:24-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json ./
COPY frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/index.html ./
COPY frontend/src ./src
RUN npm install && npm run build

FROM debian:bookworm-slim AS cpp-builder
WORKDIR /usr/src/ultra_low_latency_option_pricer/cpp
COPY cpp/Makefile ./
COPY cpp/main.cpp ./
RUN apt-get update && apt-get install -y g++ make && make && rm -rf /var/lib/apt/lists/*

FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
COPY --from=cpp-builder /usr/src/ultra_low_latency_option_pricer/cpp/option_pricer ./cpp/option_pricer
COPY app.py ./
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]

