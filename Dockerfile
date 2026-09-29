FROM ghcr.io/astral-sh/uv:0.12.19-python3.14-alpine3.23
ENV PATH="/app/.venv/bin:$PATH" PYTHONOPTIMIZE=2 UV_NO_CACHE=1 UV_NO_DEV=1
RUN apk add --no-cache git tini

WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-install-project --compile-bytecode
COPY kzkitty ./kzkitty

ENTRYPOINT ["/sbin/tini", "--"]
CMD ["python", "-m", "kzkitty"]
