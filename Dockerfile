FROM python:3.12-slim

# System dependencies for Pillow, GDAL-light, fonts
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gcc \
        libc-dev \
        libgdal-dev \
        libgeos-dev \
        libproj-dev \
        fonts-dejavu-core \
        curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create output directory
RUN mkdir -p output_onderlegger

# Expose Streamlit port + health check port
EXPOSE 8501 8082

# Health check: hit Streamlit every 30 s
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8501/_stcore/health || exit 1

# Run both the health check server and Streamlit
CMD ["sh", "-c", "python healthcheck.py & streamlit run app/main.py --server.port=8501 --server.address=0.0.0.0 --server.headless=true"]
