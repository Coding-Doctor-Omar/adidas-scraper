# 1. Force amd64 platform to prevent architecture/loader crashes on Mac M1/M2/M3 or ARM servers
FROM --platform=linux/amd64 apify/actor-python:3.13

USER root

# 2. Install all core Linux graphics & rendering dependencies as root
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    ca-certificates \
    libglib2.0-0 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound22 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

USER myuser

# 3. Copy requirements and install packages
COPY --chown=myuser:myuser requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 4. Download Chromiumfish binaries and grant executable permissions to ALL sub-files
RUN chromiumfish fetch \
 && find /home/myuser/.cache/chromiumfish -type f -exec chmod +x {} +

# 5. Copy project source code
COPY --chown=myuser:myuser . ./

# 6. Verify compilation
RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]