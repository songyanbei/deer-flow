FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/backend/.venv/bin:/root/.local/bin:${PATH}"

RUN python -m pip install --no-cache-dir uv

WORKDIR /app

COPY backend ./backend
COPY skills ./skills

RUN cd /app/backend && uv sync --frozen --no-dev

WORKDIR /app/backend

EXPOSE 8001 2024

CMD ["uvicorn", "src.gateway.app:app", "--host", "0.0.0.0", "--port", "8001"]
