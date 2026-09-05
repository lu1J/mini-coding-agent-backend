FROM python:3.13-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Existing Git tools need the git executable; no repository is initialized here.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 agent

COPY requirements.txt .
RUN python -m pip install --no-cache-dir -r requirements.txt \
    && python -m pip check

COPY app ./app
COPY main.py .
# Optional demos and offline evaluation use the same image.
COPY scripts ./scripts
COPY evals ./evals
COPY run_eval.py eval_tasks.json VERSION ./
RUN mkdir -p /app/workspace && chown agent:agent /app/workspace

USER agent
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
