# Guildhall web panel image.
FROM python:3.12-slim

# uv for reproducible, locked dependency installs (resolved set lives in uv.lock).
COPY --from=ghcr.io/astral-sh/uv:0.11.24 /uv /uvx /bin/

# Don't write .pyc at build/runtime; unbuffered stdout for container logs. Build
# the venv with copied wheels (no cross-layer hardlinks) and compiled bytecode
# for faster cold starts. Put the synced venv on PATH so gunicorn runs directly.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Install the locked runtime dependencies first, for layer caching. --frozen
# fails the build if pyproject.toml and uv.lock have drifted; --no-dev omits the
# pytest dev extra. The project itself is not packaged (tool.uv.package = false),
# so only pyproject.toml + uv.lock are needed here -- no app code yet.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Copy the application (config.toml/.env are excluded via .dockerignore;
# configuration comes from environment variables at runtime).
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 5000

# Production WSGI server. wsgi.py builds app = create_app(), which reads config
# from GUILDHALL_* env vars. gunicorn is on PATH via the synced .venv.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", \
     "--access-logfile", "-", "--forwarded-allow-ips", "*", "wsgi:app"]
