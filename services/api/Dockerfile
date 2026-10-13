FROM python:3.12-slim
# ffprobe is only used on demand to inspect completed downloaded media.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt && \
    useradd --uid 10001 --create-home appuser
COPY app.py ./
COPY torrents.py ./
COPY discovery.py ./
COPY media.py ./
COPY media_routes.py ./
COPY probe.py ./
COPY static ./static
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
