# First, specify the base Docker image.
# You can see the Docker images from Apify at https://hub.docker.com/r/apify/.
# You can also use any other image from Docker Hub.
FROM apify/actor-python-playwright-camoufox:3.14-1.62.0
RUN playwright install-deps chromium

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
 && echo "All installed Python packages:" \
  && test -f "$HOME/.cache/chromiumfish/geoip/ip2tz-2026.08.bin" \
 && pip freeze

COPY --chown=myuser:myuser . ./

RUN python -m compileall -q my_actor/

CMD ["python", "-m", "my_actor"]
