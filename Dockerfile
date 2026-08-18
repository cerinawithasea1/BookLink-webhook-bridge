FROM python:3.11-slim

WORKDIR /app

# OS deps for Telethon optional dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY notifier.py .
COPY .env.example .

VOLUME ["/data"]

CMD ["python", "notifier.py"]
