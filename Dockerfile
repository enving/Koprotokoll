FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright \
    KOPROTOKOLL_DATA=/data \
    HF_HOME=/data/models/huggingface \
    KOPROTOKOLL_REQUIRE_KEY=1

WORKDIR /app
COPY requirements.txt requirements.txt
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && pip install -r requirements.txt icalendar recurring-ical-events \
    && python -m playwright install --with-deps chromium \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 koprotokoll \
    && mkdir /data \
    && chown koprotokoll:koprotokoll /data

COPY koprotokoll/ koprotokoll/
COPY scripts/download_models.py scripts/download_models.py
USER koprotokoll
VOLUME ["/data"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)"
CMD ["python", "-m", "koprotokoll", "--host", "0.0.0.0", "--port", "8000"]
