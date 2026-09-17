FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system alos && useradd --system --gid alos --home-dir /app alos

COPY pyproject.toml README.md ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./

RUN python -m pip install --upgrade pip && python -m pip install .

USER alos
EXPOSE 8000

CMD ["uvicorn", "alos.main:app", "--host", "0.0.0.0", "--port", "8000"]
