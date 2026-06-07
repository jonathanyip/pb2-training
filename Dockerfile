FROM python:3.11-slim

# ffmpeg/ffprobe are required for video probing and frame sampling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY pb2core /app/pb2core
COPY pb2app /app/pb2app
COPY config.yaml config.docker.yaml /app/
RUN pip install --no-cache-dir .

# Persist everything (SQLite DB, videos, frames, models, datasets) under /data,
# which is backed by a Docker volume. See README "Deploy with Docker".
ENV PB2_CONFIG=/app/config.docker.yaml
VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "pb2app.main:app", "--host", "0.0.0.0", "--port", "8000"]
