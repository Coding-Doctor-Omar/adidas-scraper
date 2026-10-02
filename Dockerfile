# First, specify the base Docker image.
FROM apify/actor-python:3.13

# Install system-level dependencies for Chromium while still root
RUN pip install playwright && playwright install-deps chromium

USER myuser

# Second, copy just requirements.txt into the Actor image
COPY --chown=myuser:myuser requirements.txt ./

# Install packages, fetch Chromiumfish binaries, and explicitly grant execution permissions
RUN echo "Python version:" \
 && python --version \
 && echo "Pip version:" \
 && pip --version \
 && echo "Installing dependencies:" \
 && pip install --no-cache-dir -r requirements.txt \
 && echo "Fetching Chromiumfish binaries:" \
 && chromiumfish fetch \
 && echo "Fixing binary permissions:" \
 && chmod -R +x /home/myuser/.cache/chromiumfish \
 && echo "All installed Python packages:" \
 && pip freeze

# Copy the remaining files and directories with the source code
COPY --chown=myuser:myuser . ./

# Ensure runnability of the Actor Python code
RUN python -m compileall -q my_actor/

# Specify launch command
CMD ["python", "-m", "my_actor"]