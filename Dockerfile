FROM python:3.11-slim-bookworm

ARG PIP_INDEX_URL
ARG PIP_TRUSTED_HOST
ARG APT_REPOSITORY=http://deb.debian.org

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=5 \
    CHROMIUM_PATH=/usr/bin/chromium

RUN sed -i "s|http://deb.debian.org|${APT_REPOSITORY}|g" \
        /etc/apt/sources.list.d/debian.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    chromium \
    ffmpeg \
    fonts-liberation \
    fonts-noto-color-emoji \
    libasound2 \
    libatk1.0-0 \
    libcairo-gobject2 \
    libdbus-glib-1-2 \
    libegl1 \
    libgbm1 \
    libgdk-pixbuf-2.0-0 \
    libgl1 \
    libgtk-3-0 \
    libnss3 \
    libpangocairo-1.0-0 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxi6 \
    libxrandr2 \
    libxrender1 \
    libxss1 \
    libxt6 \
    libxtst6 \
    procps \
    tini \
    xauth \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir .

RUN useradd --create-home --uid 10001 service \
    && chown -R service:service /app /home/service
RUN XDG_CACHE_HOME=/home/service/.cache python -m camoufox fetch \
    && chown -R service:service /home/service/.cache
USER service

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.getenv('PORT', '3000') + '/health/live', timeout=3)"

ENTRYPOINT ["tini", "--"]
CMD ["xvfb-run", "-a", "--server-args=-screen 0 1920x1080x24", "python", "-m", "camoufox_service"]
