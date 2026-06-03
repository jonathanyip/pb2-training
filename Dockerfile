FROM python:3.11-slim

WORKDIR /app
COPY pyproject.toml README.md /app/
COPY pb2core /app/pb2core
COPY pb2app /app/pb2app
COPY config.yaml /app/config.yaml
RUN pip install --no-cache-dir .

ENV PB2_CONFIG=/app/config.yaml
EXPOSE 8000
CMD ["uvicorn", "pb2app.main:app", "--host", "0.0.0.0", "--port", "8000"]
