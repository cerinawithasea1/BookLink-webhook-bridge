FROM python:3.11-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY consumer/requirements.txt ./
RUN pip install --no-cache-dir -r consumer/requirements.txt

COPY consumer/app.py ./

VOLUME ["/data"]

CMD ["python", "app.py"]
