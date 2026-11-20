FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY backend ./backend
COPY static ./static

RUN pip install --no-cache-dir .

ENV HOST=0.0.0.0 \
    PORT=8765 \
    BRUNO_DB_PATH=/data/bruno.db \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 8765

CMD ["sh", "-c", "uvicorn backend.main:app --host 0.0.0.0 --port ${PORT:-8765}"]
