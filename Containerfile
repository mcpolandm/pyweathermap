# Containerfile
#
# Build:
#   podman build -t pyweathermap:latest --no-cache .
#
# Run:
#   podman run -d --name pyweathermap -p 8888:8888 -e LIBRENMS_URL=URL -e LIBRENMS_API_KEY=KEY pyweathermap:latest
#
# --no-cache (or bumping CACHEBUST) is required to pick up new commits.
# This container will pull scripts directly from repo, NOT from any local copies

FROM python:3.11-slim

# repo address
ARG PYWEATHERMAP_REPO=https://github.com/mcpolandm/pyweathermap.git
ARG PYWEATHERMAP_REF=main
ARG CACHEBUST=1

# Install git, snmp, fonts for project
RUN apt-get update \
    && apt-get install -y --no-install-recommends git snmp fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Copies GitHub repo files into container
RUN git clone --depth 1 --branch "${PYWEATHERMAP_REF}" "${PYWEATHERMAP_REPO}" /app

WORKDIR /app

# Installs python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copies local inventory/ file in-keep switch list and any neighbor files here.
# Be sure switch list has appropriate neighbor file paths.
COPY inventory/ /data/inventory/

# Container runs with non-root user
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /data
USER appuser

# Set environment variables
ENV PYWEATHERMAP_SWITCHES=/data/inventory/switch_list.txt
# Update to accurate switch, or pass as environment variable in run command
ENV PYWEATHERMAP_DEFAULT_CENTER=10.0.0.0

EXPOSE 8888

ENTRYPOINT ["python", "main.py"]
CMD ["--server", "--host", "0.0.0.0"]