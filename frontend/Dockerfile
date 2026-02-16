# Use a slim Python base image
FROM python:3.12-slim

# Avoid Python writing .pyc files and enable unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cloud Run uses PORT env var. Default locally is 8080.
ENV PORT=8080

WORKDIR /app

# System deps (optional but useful for many Python libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps first (better layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the app
COPY . .

# Expose Cloud Run port (optional, informational)
EXPOSE 8080

# Start Chainlit on Cloud Run
# --host 0.0.0.0 is required inside containers
# --port must be PORT for Cloud Run
CMD ["sh", "-c", "chainlit run app.py --host 0.0.0.0 --port ${PORT}"]