FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    libgomp1 \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONUNBUFFERED=1 \
    HOST=0.0.0.0 \
    PORT=8000 \
    MODEL_CACHE_DIR=/app/data/model_cache \
    DB_MOUNT_DIR=/app/data/db_mounts \
    HF_HOME=/app/data/model_cache \
    TRANSFORMERS_CACHE=/app/data/model_cache \
    HUGGINGFACE_HUB_CACHE=/app/data/model_cache \
    AUTO_RESTORE_SNAPSHOT=false

EXPOSE 8000

CMD ["python", "run.py"]
