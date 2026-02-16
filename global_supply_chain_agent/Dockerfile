FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Cloud Run provides PORT (default 8080). We'll respect it.
ENV PORT=8080

WORKDIR /app

# Optional system packages (curl useful for health/debug)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
  && rm -rf /var/lib/apt/lists/*

# Install Python deps first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app code
COPY . .

# Cloud Run listens on $PORT
EXPOSE 8080

# Start ADK API Server.
# IMPORTANT: AGENTS_DIR must be the directory whose children are agent folders. [2](https://codemagnet.in/2024/04/03/how-to-plot-google-map-using-folium-package-in-python-full-tutorial/)[3](https://www.geeksforgeeks.org/python/python-plotting-google-map-using-folium-package/)
# Also bind to 0.0.0.0 inside container, and use Cloud Run's $PORT.
CMD ["sh", "-c", "adk api_server agents/sub_agents --host 0.0.0.0 --port ${PORT}"]