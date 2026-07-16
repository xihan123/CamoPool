FROM ghcr.io/astral-sh/uv:python3.13-trixie-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    CAMOPOOL_HEADLESS=true \
    CAMOPOOL_BROWSER_OS=linux \
    CAMOPOOL_CAMOUFOX_BROWSER=official/152.0.4-beta.26 \
    CAMOPOOL_WORKER_HOST=127.0.0.1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml uv.lock .python-version ./

RUN uv sync -v --frozen --no-dev --no-install-project

RUN printf 'Acquire::Retries "5";\nAcquire::http::Timeout "30";\n' \
        > /etc/apt/apt.conf.d/80-camopool \
    && playwright install-deps firefox \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 camopool

COPY camopool ./camopool
RUN uv sync -v --frozen --no-dev --no-editable

USER camopool
RUN camoufox sync \
    && printf 'y\n' | camoufox fetch "$CAMOPOOL_CAMOUFOX_BROWSER" \
    && python -c "from camoufox.pkgman import installed_verstr; print(installed_verstr())"

EXPOSE 8000 53333

CMD ["camopool"]
