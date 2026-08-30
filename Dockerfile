# PACT, in one container: the four services and the console behind one port.
#
# Two stages so the Node toolchain does not ship: the first builds the console,
# the second runs Python and copies the built assets across.

# ---------------------------------------------------------------- console ---
FROM node:20-slim AS console

# The repo layout is mirrored rather than flattened. The console imports
# `../../../contracts/generated`, which only resolves if console/ sits beside
# contracts/ exactly as it does in the repo. Flattening it here would work until
# someone adds a second cross-directory import.
WORKDIR /build/console

# Manifests first, so `npm ci` stays cached until a dependency actually changes.
COPY console/package.json console/package-lock.json ./
RUN npm ci

COPY contracts/generated.ts /build/contracts/generated.ts
COPY console/ ./
RUN npx vite build


# ---------------------------------------------------------------- runtime ---
FROM python:3.12-slim AS runtime

# Nothing here needs a compiler at runtime; the wheels are all manylinux.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY contracts/ ./contracts/
COPY core/ ./core/
COPY rails/ ./rails/
COPY merchant/ ./merchant/
COPY buyer/ ./buyer/
COPY sim/ ./sim/
COPY eval/ ./eval/
COPY deploy/ ./deploy/
COPY profiles/ ./profiles/
COPY fixtures/ ./fixtures/
COPY scripts/ ./scripts/
COPY pyproject.toml ./

COPY --from=console /build/console/dist ./console/dist

# The database and the gate's signing key live here. Mount a volume on it or
# every restart issues a new signing key and forgets every mandate — survivable
# for a demo, wrong for anything else.
RUN mkdir -p /data && chmod 700 /data
ENV PACT_DB_URL=sqlite:////data/pact.db \
    PACT_GATE_KEY_PATH=/data/gate_signing_key.hex \
    PACT_PROFILE=razorpay-track01 \
    PORT=8080

# Do not run as root. The app writes only to /data.
RUN useradd --create-home --uid 10001 pact && chown -R pact:pact /app /data
USER pact

EXPOSE 8080

# The healthcheck hits the composed endpoint, which reports the rail, the
# auditor mode and whether the console was built — so a container that is up but
# misconfigured is visible rather than merely green.
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=2).status==200 else 1)"

# One worker, deliberately. The ledger's correctness rests on SQLite's write
# lock, the rail's idempotency table is in memory, and the saga runs as a
# background task inside the process — none of which survive being spread across
# workers. Scaling this means Postgres and a real queue, not `--workers 4`.
CMD ["python", "-m", "uvicorn", "deploy.app:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
