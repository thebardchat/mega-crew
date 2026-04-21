FROM mega-crew-base:latest
# Weld needs docker CLI to restart containers after code changes (Phase 4)
RUN apt-get update && apt-get install -y --no-install-recommends docker.io && rm -rf /var/lib/apt/lists/*
COPY weld/bot.py weld/knowledge.py weld/config.json ./
CMD ["python", "bot.py"]
