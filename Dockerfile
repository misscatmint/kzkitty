FROM ghcr.io/astral-sh/uv:0.12.17-python3.14-alpine3.23
ENV PYTHONOPTIMIZE=2 UV_NO_CACHE=1 UV_NO_DEV=1
RUN apk add --no-cache git tini

WORKDIR /app
COPY . /app
RUN uv sync --locked --compile-bytecode

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["uv", "run", "-m", "kzkitty"]
