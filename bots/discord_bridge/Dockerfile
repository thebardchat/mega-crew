FROM mega-crew-base:latest

# Discord bridge needs discord.py + aiohttp on top of the base image
RUN pip install --no-cache-dir "discord.py>=2.3.0,<3.0" "aiohttp>=3.9.0"

# Match the established convention: COPY into the WORKDIR (/app), run python bot.py
COPY discord_bridge/bot.py discord_bridge/knowledge.py discord_bridge/config.json ./

# characters.json is the crew roster — same dir for runtime resolution
COPY characters.json ./

CMD ["python", "bot.py"]
