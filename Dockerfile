# Herald headless runner (CLI) - no GUI, no Qt.
#
# Build:  docker build -t herald .
# Run:    docker run -d --name herald \
#           -v $PWD/herald-data:/app/data \
#           -v /path/to/releases:/releases:ro \
#           herald
#
# The mounted /app/data must contain the hubs.json you configured with the GUI
# (Settings -> Target runtime OS = Linux, watch paths as they are IN the
# container, e.g. /releases/...). See docker-compose.yml for a full example.

FROM python:3.12-slim

# Only the core (headless) deps - no PySide6/Qt.
COPY requirements-core.txt /tmp/requirements-core.txt
RUN pip install --no-cache-dir -r /tmp/requirements-core.txt \
    && rm /tmp/requirements-core.txt

WORKDIR /app

# Just the pure-Python engine + the headless runner. The GUI (main.py, ui/,
# assets/, PySide6) is deliberately NOT in the image, and neither is the
# local-only cli_login.py smoke test (it is gitignored, so it is not in a CI
# checkout - COPYing it would break the image build).
COPY adc/ /app/adc/
COPY herald_cli.py /app/

# config.py resolves data/ to /app/data (next to the package) when not frozen;
# mount your operator config there.
RUN mkdir -p /app/data \
    && useradd -u 1000 -M -d /app -s /usr/sbin/nologin herald \
    && chown -R herald:herald /app
VOLUME ["/app/data"]
USER herald

# SIGTERM triggers Herald's clean shutdown (disconnect every hub).
STOPSIGNAL SIGTERM

# Runs every ENABLED hub in data/hubs.json. Append flags after the image name to
# override, e.g. `docker run herald --hub "My hub" -v`.
ENTRYPOINT ["python", "-u", "herald_cli.py"]
