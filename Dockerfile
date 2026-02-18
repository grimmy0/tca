FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN python -m pip install --upgrade pip && \
    python -m pip install \
    "fastapi>=0.115,<1.0" \
    "uvicorn[standard]>=0.30,<1.0" \
    "sqlalchemy>=2.0,<3.0" \
    "aiosqlite>=0.20,<1.0" \
    "alembic>=1.13,<2.0" \
    "telethon>=1.42,<1.43" \
    "jinja2>=3.1,<4.0" \
    "rapidfuzz>=3.9,<4.0" \
    "argon2-cffi>=23.1,<24.0" \
    "cryptography>=43,<45"

COPY alembic ./alembic
COPY tca ./tca
COPY alembic.ini ./alembic.ini

RUN mkdir -p /data

EXPOSE 8787

ENV TCA_DB_PATH=/data/tca.db
ENV TCA_BIND=0.0.0.0

CMD ["python", "-m", "uvicorn", "tca.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8787"]
