FROM node:24-slim AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
COPY frontend/tsconfig.json frontend/tsconfig.node.json frontend/vite.config.ts ./
COPY frontend/index.html ./
COPY frontend/src ./src
RUN npm install && npm run build

FROM debian:bookworm-slim AS cpp-builder
WORKDIR /usr/src/ultra_low_latency_option_pricer/cpp
COPY cpp/Makefile ./
COPY cpp/main.cpp ./
RUN apt-get update && apt-get install -y g++ make && make && rm -rf /var/lib/apt/lists/*

FROM node:24-slim
WORKDIR /app
COPY --from=frontend-builder /app/frontend/dist ./frontend/dist
COPY --from=frontend-builder /app/frontend/node_modules ./frontend/node_modules
COPY --from=cpp-builder /usr/src/ultra_low_latency_option_pricer/cpp/option_pricer ./cpp/option_pricer
COPY frontend/server.js ./frontend/server.js
COPY frontend/package.json ./frontend/package.json
COPY frontend/package-lock.json ./frontend/package-lock.json
WORKDIR /app/frontend
EXPOSE 5174
CMD ["node", "server.js"]
