# A single small image running the web app. The weekly scan runs in the same
# image via `docker compose run`, so there is one thing to build and keep current.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first, so a code change does not reinstall the world.
COPY pyproject.toml README.md ./
COPY aadfs ./aadfs
RUN pip install --no-cache-dir . && \
    adduser --disabled-password --gecos "" --uid 10001 aadfs && \
    mkdir -p /data && chown -R aadfs:aadfs /app /data

# Boards and the results database live on a mounted volume, not in the image.
ENV AADFS_DATA=/data
VOLUME ["/data"]

USER aadfs
EXPOSE 8000

# Binding to 0.0.0.0 inside the container is fine: the port is only published
# to the reverse proxy. The app still requires AADFS_PASSWORD to do it.
CMD ["aadfs", "serve", "--host", "0.0.0.0", "--port", "8000"]
