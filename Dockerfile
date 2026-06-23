# Guildhall web panel image.
FROM python:3.12-slim

# Don't write .pyc files; unbuffered stdout for container logs.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install dependencies first for better layer caching.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application (config.toml/.env are excluded via .dockerignore;
# configuration comes from environment variables at runtime).
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

EXPOSE 5000

# Production WSGI server. wsgi.py builds app = create_app(), which reads config
# from GUILDHALL_* env vars.
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "3", \
     "--access-logfile", "-", "--forwarded-allow-ips", "*", "wsgi:app"]
