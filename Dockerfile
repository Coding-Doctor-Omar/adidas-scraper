FROM apify/actor-python:3.13
RUN pip install playwright && playwright install-deps chromium

# Pin the geoip DB: no "latest" lookup at runtime, no download at runtime
ENV CHROMIUMFISH_GEOIP_VERSION=2026.08

# Get the crashpad handler from Debian's chromium-common (extract only, don't install)
RUN apt-get update \
 && cd /tmp \
 && apt-get download chromium-common \
 && dpkg-deb -x chromium-common_*.deb /tmp/cc \
 && mkdir -p /opt/crashpad \
 && cp "$(find /tmp/cc -name chrome_crashpad_handler -type f | head -n1)" /opt/crashpad/ \
 && chmod 755 /opt/crashpad/chrome_crashpad_handler \
 && rm -rf /tmp/cc /tmp/chromium-common_*.deb /var/lib/apt/lists/*

USER myuser

COPY --chown=myuser:myuser requirements.txt ./

RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install -r requirements.txt \
 && chromiumfish fetch \
  && python -c "from chromiumfish import fetch_db; print(fetch_db('2026.08'))" \
 && CHROME_DIR="$(dirname "$(find "$HOME/.cache/chromiumfish" -type f -name chrome | head -n1)")" \
 && cp /opt/crashpad/chrome_crashpad_handler "$CHROME_DIR/" \
 && test -x "$CHROME_DIR/chrome" \
 && test -x "$CHROME_DIR/chrome_crashpad_handler" \
 && test -f "$HOME/.cache/chromiumfish/geoip/ip2tz-2026.08.bin" \
 && echo "All installed Python packages:" \
 && pip freeze

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]