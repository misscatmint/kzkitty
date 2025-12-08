FROM python:3.14.0-alpine
COPY --from=ghcr.io/astral-sh/uv:0.9.13 /uv /uvx /bin/

WORKDIR /app

ENV UV_SYSTEM_PYTHON=1

COPY requirements.txt .
RUN uv pip install -r requirements.txt

COPY . .
CMD ["python", "-OO", "-m", "kzkitty"]
