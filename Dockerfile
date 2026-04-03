FROM ghcr.io/astral-sh/uv:0.11.3-python3.14-alpine3.23
ENV PYTHONOPTIMIZE=2 UV_COMPILE_BYTECODE=1 UV_NO_CACHE=1 UV_NO_DEV=1

WORKDIR /app
COPY . /app
RUN uv sync --locked

CMD ["uv", "run", "-m", "kzkitty"]
