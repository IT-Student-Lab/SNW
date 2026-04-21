## ===== Stage 1: build React frontend =====
FROM node:20-slim AS frontend-build
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ .
RUN npm run build

## ===== Stage 2: Python backend =====
FROM python:3.12-slim

# System dependencies for Pillow, GDAL-light, fonts, and Xvfb (for ODA File Converter)
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc-dev \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        fonts-dejavu-core \
        xvfb \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Force matplotlib to use non-interactive backend (no X11/DISPLAY needed)
ENV MPLBACKEND=Agg

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Copy frontend build into the image
COPY --from=frontend-build /build/dist /app/frontend/dist

# Create output directory
RUN mkdir -p output_onderlegger

# Expose FastAPI port + health check port
EXPOSE 8009 8082

# Health check: FastAPI /health endpoint
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8009/health || exit 1

# Run both the health check server and FastAPI
CMD ["sh", "-c", "python healthcheck.py & uvicorn api.main:app --host 0.0.0.0 --port 8009"]
