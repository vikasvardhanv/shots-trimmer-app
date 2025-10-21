# Stage 1: Builder (install deps and build)
FROM python:3.11-slim AS builder

# Set environment variables (early for consistency)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    wget \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Set work directory
WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

# Install Python dependencies (pin moviepy to 1.0.3)
RUN pip install --upgrade pip setuptools wheel && \
    pip install --no-cache-dir decorator proglog tqdm imageio imageio-ffmpeg numpy moviepy==1.0.3 && \
    pip install --no-cache-dir -r requirements.txt && \
    find /usr/local -depth \( \( -type d -a \( -name test -o -name tests -o -name __pycache__ \) \) -o \( -type f -a \( -name '*.pyc' -o -name '*.pyo' \) \) \) -exec rm -rf '{}' +  # Clean up Python caches

# Stage 2: Runtime (slim image with only essentials)
FROM python:3.11-slim

# Set environment variables (repeat for runtime)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    TZ=UTC

# Install runtime system dependencies (FFmpeg with libx264 for MoviePy video encoding)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libx264-dev \
    && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*

# Create non-root user for security
RUN useradd -m appuser && mkdir -p /app/uploads /app/downloads /app/logs /app/instance

# Set work directory and ownership
WORKDIR /app
COPY --from=builder --chown=appuser:appuser /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder --chown=appuser:appuser /usr/local/bin /usr/local/bin
COPY --chown=appuser:appuser . .

# Set permissions
RUN chmod +x gunicorn.conf.py || true && \
    chown -R appuser:appuser /app/uploads /app/downloads /app/logs /app/instance

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 8000

# Start application
CMD ["gunicorn", "--config", "gunicorn.conf.py", "app:app"]