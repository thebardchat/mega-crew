"""
discord_bridge — Shane's MEGA Crew Discord I/O channel.

This is NOT a 17-bots-on-Discord setup. It's ONE Discord application
that wears 17 different faces via webhooks. Each crew member speaks
with their own name, avatar, and voice — but underneath, this single
bridge routes traffic in and out of the existing SQLite bus.

INBOUND:  Discord DM / channel mention → bus.push(sender="discord", recipient="<bot>", payload)
OUTBOUND: bus.pull(recipient="discord") → POST to that bot's webhook URL

Each crew member's avatar is loaded from the public GitHub raw URL:
  https://raw.githubusercontent.com/thebardchat/mega-crew/main/cards/mega_front/<name>_mega_front.png

Slash-style commands (also work in DMs as plain text):
  /godmode     → write IgnitionEvent to Weaviate (surface=discord)
  /recent      → last 5 IgnitionEvents
  /who         → list the crew + roles
  /log <text>  → quick log moment
  /mood <tag>  → quick mood check

Routing pattern:
  "arc check the queue"   → routes to arc
  "@volt scan drift"      → routes to volt
  "gemini six months"     → routes to gemini_strategist
  (anything else)         → broadcast to bus with recipient="crew" — Arc picks it up

Author: Shane Brazelton + Claude
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Shared crew modules (bus, bot_base, mega_client) are on PYTHONPATH=/app via base image
import aiohttp
import discord
from discord import app_commands

import bus  # SQLite message bus shared with the rest of the crew

# ──────────────────────────────────────────────────────────────────────
# Configuration — env vars (set via docker-compose.yml or .env)
# ──────────────────────────────────────────────────────────────────────
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
WEAVIATE_URL  = os.environ.get("WEAVIATE_URL", "http://100.100.90.66:8080")
GITHUB_RAW    = "https://raw.githubusercontent.com/thebardchat/mega-crew/main/cards/mega_front"
MEGA_BASE     = Path(os.environ.get("MEGA_BASE", "/mega"))
CHARACTERS_JSON = Path(os.environ.get("CHARACTERS_JSON", "characters.json"))
POLL_INTERVAL_SEC = float(os.environ.get("POLL_INTERVAL_SEC", "2.0"))

STATUS_FILE = MEGA_BASE / "status" / "discord_bridge.json"
STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] discord_bridge: %(message)s",
)
log = logging.getLogger("discord_bridge")

# ──────────────────────────────────────────────────────────────────────
# Load the crew roster (characters.json)
# ──────────────────────────────────────────────────────────────────────
def load_characters() -> dict:
    """Read the crew roster. Returns dict keyed by bot id."""
    if not CHARACTERS_JSON.exists():
        log.error("characters.json not found at %s", CHARACTERS_JSON)
        return {}
    try:
        return json.loads(CHARACTERS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to parse characters.json: %s", e)
        return {}

CHARACTERS = load_characters()
CREW_NAMES = list(CHARACTERS.keys())  # ['sparky', 'arc', 'weld', ...]

def webhook_env_for(bot_id: str) -> str:
    """e.g. arc → WEBHOOK_ARC, gemini_strategist → WEBHOOK_GEMINI_STRATEGIST."""
    return f"WEBHOOK_{bot_id.upper()}"

def avatar_url_for(bot_id: str) -> str:
    """Public GitHub raw URL for the bot's mega_front card."""
    return f"{GITHUB_RAW}/{bot_id}_mega_front.png"

def display_name_for(bot_id: str) -> str:
    """The crew member's actual name from characters.json, or capitalized id."""
    return CHARACTERS.get(bot_id, {}).get("name", bot_id.replace("_", " ").title())

def color_for(bot_id: str) -> int:
    """Hex color from characters.json, parsed to Discord int."""
    hex_color = CHARACTERS.get(bot_id, {}).get("color", "#ff5500")
    return int(hex_color.lstrip("#"), 16)

# ──────────────────────────────────────────────────────────────────────
# Bot identity routing — find which crew member a message is for
# ──────────────────────────────────────────────────────────────────────
def detect_recipient(text: str) -> str | None:
    """
    Look at the first 1-2 words of text. If they match a crew member id or name,
    return the canonical id. Else return None (caller falls back to 'crew' broadcast).
    """
    if not text:
        return None
    cleaned = text.strip().lstrip("@/").lower()
    if not cleaned:
        return None
    first = cleaned.split()[0].rstrip(":,.!?")
    if first in CREW_NAMES:
        return first
    # Match by display name ("gemini" → gemini_strategist)
    for bot_id, character in CHARACTERS.items():
        name_lower = character.get("name", "").lower()
        if name_lower == first or bot_id.startswith(first + "_"):
            return bot_id
    return None

def pick_response_line(bot_id: str, kind: str = "action") -> str:
    """
    Pull a dialogue line from the bot's character entry.
    kind=action → dialogue[0] (confirmation/work)
    kind=fear   → dialogue[1] (doubt)
    kind=dream  → dialogue[2] (aspiration)
    """
    c = CHARACTERS.get(bot_id, {})
    dlg = c.get("dialogue", [])
    if not dlg:
        return c.get("catchphrase", "Acknowledged.")
    idx = {"action": 0, "fear": 1, "dream": 2}.get(kind, 0)
    return dlg[idx] if idx < len(dlg) else dlg[0]

# ──────────────────────────────────────────────────────────────────────
# Weaviate writes — IgnitionEvent class
# ──────────────────────────────────────────────────────────────────────
async def write_ignition_event(session: aiohttp.ClientSession, props: dict) -> str | None:
    """POST an IgnitionEvent to Weaviate. Returns the new object id."""
    payload = {
        "class": "IgnitionEvent",
        "properties": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "host": "shanebrain",
            **props,
        },
    }
    try:
        async with session.post(
            f"{WEAVIATE_URL}/v1/objects",
            json=payload,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status >= 300:
                body = await r.text()
                log.warning("Weaviate write %s: %s", r.status, body[:200])
                return None
            data = await r.json()
            return data.get("id")
    except Exception as e:
        log.warning("Weaviate write failed: %s", e)
        return None

async def query_recent_ignitions(session: aiohttp.ClientSession, limit: int = 5) -> list[dict]:
    q = {
        "query": "{Get{IgnitionEvent(limit:" + str(limit) +
                 ',sort:[{path:["timestamp"],order:desc}])'
                 "{timestamp surface host sobriety_days verse_ref action_taken mood notes}}}"
    }
    try:
        async with session.post(
            f"{WEAVIATE_URL}/v1/graphql",
            json=q,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            data = await r.json()
            return data.get("data", {}).get("Get", {}).get("IgnitionEvent", []) or []
    except Exception as e:
        log.warning("Weaviate query failed: %s", e)
        return []

# ──────────────────────────────────────────────────────────────────────
# Outbound — post to a bot's webhook
# ──────────────────────────────────────────────────────────────────────
async def post_as_crew(
    session: aiohttp.ClientSession,
    bot_id: str,
    content: str,
    embed: dict | None = None,
) -> bool:
    """Post a message via the crew member's Discord webhook."""
    url = os.environ.get(webhook_env_for(bot_id), "")
    if not url:
        log.warning("No webhook configured for %s (env %s)", bot_id, webhook_env_for(bot_id))
        return False
    body = {
        "username": display_name_for(bot_id),
        "avatar_url": avatar_url_for(bot_id),
        "content": content[:1900],  # Discord 2000-char limit, leave buffer
    }
    if embed:
        body["embeds"] = [embed]
    try:
        async with session.post(url, json=body, timeout=aiohttp.ClientTimeout(total=10)) as r:
            if r.status >= 300:
                log.warning("Webhook %s returned %s", bot_id, r.status)
                return False
            return True
    except Exception as e:
        log.warning("Webhook post failed for %s: %s", bot_id, e)
        return False

# ──────────────────────────────────────────────────────────────────────
# Status file — Flux watches this
# ──────────────────────────────────────────────────────────────────────
def write_status(state: str, action: str):
    now = datetime.now(timezone.utc).isoformat()
    STATUS_FILE.write_text(json.dumps({
        "status": state,
        "last_run": now,
        "last_action": action,
        "zone": "external_io",
        "interval_seconds": int(POLL_INTERVAL_SEC),
        "next_run": now,
    }, indent=2))

# ──────────────────────────────────────────────────────────────────────
# Discord client
# ──────────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True

class CrewDiscord(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)
        self.session: aiohttp.ClientSession | None = None

    async def setup_hook(self):
        # Persistent HTTP session for Weaviate + webhooks
        self.session = aiohttp.ClientSession()
        # Background task: drain bus messages addressed to "discord"
        self.loop.create_task(self.drain_bus_loop())
        # Sync slash commands globally (may take ~1hr to propagate first time)
        try:
            await self.tree.sync()
        except Exception as e:
            log.warning("Slash sync failed: %s", e)
        write_status("OK", "Discord bridge online")

    async def on_ready(self):
        log.info("Connected as %s (id=%s) — %d crew members loaded",
                 self.user, self.user.id if self.user else "?", len(CHARACTERS))
        write_status("OK", f"Connected as {self.user}")

    async def on_message(self, msg: discord.Message):
        # Ignore self and other bots
        if msg.author == self.user or msg.author.bot:
            return
        # Only respond to DMs or @mentions in guilds
        is_dm = isinstance(msg.channel, discord.DMChannel)
        is_mention = self.user in msg.mentions
        if not (is_dm or is_mention):
            return

        text = msg.content
        # Strip @bot prefix if present
        for m in msg.mentions:
            text = text.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        text = text.strip()

        log.info("DM from %s: %s", msg.author, text[:80])

        # Slash-style command dispatch (works in DMs as plain text too)
        lower = text.lower().strip()
        if lower.startswith("/who"):
            return await self.cmd_who(msg)
        if lower.startswith("/recent"):
            return await self.cmd_recent(msg)
        if lower.startswith("/godmode"):
            return await self.cmd_godmode(msg, text)
        if lower.startswith("/log"):
            return await self.cmd_log(msg, text[4:].strip())
        if lower.startswith("/mood"):
            return await self.cmd_mood(msg, text[5:].strip())

        # Route to a specific crew member if named in first word
        recipient = detect_recipient(text)
        if recipient:
            return await self.dispatch_to_crew(msg, recipient, text)

        # Otherwise broadcast — Arc will pick up
        await self.dispatch_to_crew(msg, "arc", text)

    # ──────────────────────────────────────────────────────────────
    # Slash / text commands
    # ──────────────────────────────────────────────────────────────
    async def cmd_who(self, msg: discord.Message):
        lines = ["**MEGA Crew Roster**"]
        for bot_id, c in CHARACTERS.items():
            lines.append(f"• **{c.get('name','?')}** — {c.get('role','?')}  ({c.get('zone','?')})")
        await msg.channel.send("\n".join(lines)[:1990])

    async def cmd_recent(self, msg: discord.Message):
        events = await query_recent_ignitions(self.session, limit=5)
        if not events:
            await msg.channel.send("No ignitions found in Weaviate (or query failed).")
            return
        lines = ["**Recent IgnitionEvents**"]
        for e in events:
            ts = e.get("timestamp", "?")
            lines.append(f"`{ts[:19]}` `{e.get('surface','?')}@{e.get('host','?')}` "
                         f"sobriety:{e.get('sobriety_days','?')} "
                         f"mood:{e.get('mood','-')}")
            if e.get("notes"):
                lines.append(f"  └─ _{e['notes'][:140]}_")
        await msg.channel.send("\n".join(lines)[:1990])

    async def cmd_godmode(self, msg: discord.Message, raw: str):
        notes = raw.replace("/godmode", "", 1).strip() or "Ignited from Discord"
        oid = await write_ignition_event(self.session, {
            "surface": "discord",
            "action_taken": "godmode_ignite",
            "arc_approved": True,
            "notes": f"[{msg.author}] {notes}",
        })
        if oid:
            await msg.channel.send(f"🔥 GOD MODE IGNITED · `{oid[:8]}` written to canonical memory.")
        else:
            await msg.channel.send("⚠️ Ignition logged locally but Weaviate write failed. Check logs.")

    async def cmd_log(self, msg: discord.Message, notes: str):
        if not notes:
            await msg.channel.send("Usage: `/log <what's on your mind>`")
            return
        oid = await write_ignition_event(self.session, {
            "surface": "discord",
            "action_taken": "log_moment",
            "arc_approved": True,
            "notes": f"[{msg.author}] {notes}",
        })
        if oid:
            await msg.channel.send(f"✍️ Logged · `{oid[:8]}`")
        else:
            await msg.channel.send("⚠️ Log write failed.")

    async def cmd_mood(self, msg: discord.Message, mood: str):
        valid = {"focused", "flow", "stuck", "tired", "grateful"}
        m = (mood or "").lower().strip()
        if m not in valid:
            await msg.channel.send(f"Mood must be one of: {', '.join(valid)}")
            return
        oid = await write_ignition_event(self.session, {
            "surface": "discord",
            "action_taken": "mood_check",
            "arc_approved": True,
            "mood": m,
            "notes": f"Mood check from {msg.author}",
        })
        await msg.channel.send(f"🧠 Mood `{m}` logged · `{(oid or '????')[:8]}`")

    # ──────────────────────────────────────────────────────────────
    # Crew dispatch — push to bus, ack to user
    # ──────────────────────────────────────────────────────────────
    async def dispatch_to_crew(self, msg: discord.Message, recipient: str, text: str):
        bus.push(
            sender="discord",
            recipient=recipient,
            payload={
                "from_user": str(msg.author),
                "channel_id": msg.channel.id,
                "channel_type": "dm" if isinstance(msg.channel, discord.DMChannel) else "guild",
                "content": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )
        # Quick ack from the recipient via their webhook (using their action dialogue)
        ack = pick_response_line(recipient, "action")
        posted = await post_as_crew(self.session, recipient,
                                    f"_{ack}_  (received from {msg.author.display_name})")
        if not posted:
            # Fallback to bot message if webhook not set
            await msg.channel.send(
                f"**{display_name_for(recipient)}:** _{ack}_  "
                f"(no webhook configured — set `{webhook_env_for(recipient)}` in env to give them their face)"
            )

    # ──────────────────────────────────────────────────────────────
    # Outbound loop — bus → Discord
    # ──────────────────────────────────────────────────────────────
    async def drain_bus_loop(self):
        log.info("Bus drain loop started (interval=%ss)", POLL_INTERVAL_SEC)
        while not self.is_closed():
            try:
                messages = await asyncio.to_thread(bus.pull, "discord", 20)
                for m in messages:
                    payload = m.get("payload", {})
                    sender = m.get("sender", "crew")
                    content = payload.get("content") or payload.get("message") or str(payload)
                    embed = payload.get("embed")
                    posted = await post_as_crew(self.session, sender, content, embed=embed)
                    if not posted:
                        log.info("Drained but couldn't post for %s — message dropped", sender)
                write_status("OK", f"drained={len(messages)}")
            except Exception as e:
                log.error("drain loop error: %s", e)
                write_status("ERROR", str(e)[:120])
            await asyncio.sleep(POLL_INTERVAL_SEC)

    async def close(self):
        if self.session:
            await self.session.close()
        await super().close()

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
def main():
    if not DISCORD_TOKEN:
        log.error("DISCORD_TOKEN env var is required. Aborting.")
        sys.exit(1)
    if not CHARACTERS:
        log.error("Empty character roster. Make sure characters.json is mounted at %s",
                  CHARACTERS_JSON)
        sys.exit(1)

    write_status("STARTING", f"Loading {len(CHARACTERS)} crew members")
    client = CrewDiscord()
    client.run(DISCORD_TOKEN, log_handler=None)

if __name__ == "__main__":
    main()
