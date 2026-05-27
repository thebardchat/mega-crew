"""
discord_bridge — Shane's MEGA Crew Discord I/O hub.

NEW ARCHITECTURE (Path B): one process runs:
  - The hub bot (MEGA Crew Bridge) — keeps channel webhooks + slash commands
  - 17 individual crew bots — each with their own token, profile, DM channel

When you DM "Arc" directly, Arc's bot receives the DM, pushes to bus.
When Arc's crew container responds, the response posts in YOUR DM with Arc.

Each crew bot identity is loaded from env:
  DISCORD_TOKEN_ARC, DISCORD_TOKEN_SPARKY, DISCORD_TOKEN_WELD, ...

The hub bot (singular) is optional now but kept for channel routing:
  DISCORD_TOKEN — the original hub bot from Path A

If DISCORD_TOKEN is unset, only the 17 individuals run.
If individual DISCORD_TOKEN_<NAME> vars are unset, those crew members
fall back to the webhook posting via the hub bot.

The bus stays the single source of truth:
  - Inbound:  push(sender="discord", recipient="<bot_id>", payload={...type: discord_message...})
  - Outbound: each crew bot drains its own queue, formats, posts in DM

Author: Shane Brazelton + Claude
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from discord import app_commands

import bus

# ──────────────────────────────────────────────────────────────────────
# Configuration
# ──────────────────────────────────────────────────────────────────────
HUB_TOKEN     = os.environ.get("DISCORD_TOKEN", "")  # the original Path-A bridge bot
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
# Load roster + per-DM webhook URLs (for fallback when no individual token)
# ──────────────────────────────────────────────────────────────────────
def load_characters() -> dict:
    if not CHARACTERS_JSON.exists():
        log.error("characters.json not found at %s", CHARACTERS_JSON)
        return {}
    try:
        return json.loads(CHARACTERS_JSON.read_text(encoding="utf-8"))
    except Exception as e:
        log.error("Failed to parse characters.json: %s", e)
        return {}

CHARACTERS = load_characters()

def webhook_env_for(bot_id: str) -> str:
    return f"WEBHOOK_{bot_id.upper()}"

def token_env_for(bot_id: str) -> str:
    return f"DISCORD_TOKEN_{bot_id.upper()}"

def avatar_url_for(bot_id: str) -> str:
    return f"{GITHUB_RAW}/{bot_id}_mega_front.png"

def display_name_for(bot_id: str) -> str:
    return CHARACTERS.get(bot_id, {}).get("name", bot_id.replace("_", " ").title())

def color_for(bot_id: str) -> int:
    return int(CHARACTERS.get(bot_id, {}).get("color", "#ff5500").lstrip("#"), 16)

def pick_response_line(bot_id: str, kind: str = "action") -> str:
    c = CHARACTERS.get(bot_id, {})
    dlg = c.get("dialogue", [])
    if not dlg:
        return c.get("catchphrase", "Acknowledged.")
    idx = {"action": 0, "fear": 1, "dream": 2}.get(kind, 0)
    return dlg[idx] if idx < len(dlg) else dlg[0]

# Sobriety days (for IgnitionEvents from /godmode-type commands)
SOB_START = datetime(2023, 11, 27, tzinfo=timezone.utc)
def sobriety_days() -> int:
    return (datetime.now(timezone.utc) - SOB_START).days

# Status file — Flux watches this
def write_status(state: str, action: str, extra: dict | None = None):
    now = datetime.now(timezone.utc).isoformat()
    body = {
        "status": state,
        "last_run": now,
        "last_action": action,
        "zone": "external_io",
        "interval_seconds": int(POLL_INTERVAL_SEC),
        "next_run": now,
    }
    if extra:
        body.update(extra)
    STATUS_FILE.write_text(json.dumps(body, indent=2))

# ──────────────────────────────────────────────────────────────────────
# Weaviate writes (IgnitionEvent)
# ──────────────────────────────────────────────────────────────────────
async def write_ignition(session: aiohttp.ClientSession, props: dict) -> str | None:
    payload = {
        "class": "IgnitionEvent",
        "properties": {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "host": "shanebrain",
            "sobriety_days": sobriety_days(),
            "verse_ref": "Joshua 1:9",
            **props,
        },
    }
    try:
        async with session.post(
            f"{WEAVIATE_URL}/v1/objects", json=payload,
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status >= 300:
                return None
            data = await r.json()
            return data.get("id")
    except Exception:
        return None

# ──────────────────────────────────────────────────────────────────────
# Payload formatter — shared by all bots' outbound paths
# ──────────────────────────────────────────────────────────────────────
def format_payload(payload: dict | str, sender: str) -> tuple[str | None, dict | None]:
    if isinstance(payload, str):
        return payload, None
    if payload.get("content"):
        return str(payload["content"]), payload.get("embed")
    if payload.get("message"):
        return str(payload["message"]), payload.get("embed")
    if payload.get("type") == "arc_rejection":
        original = payload.get("original_type") or "unknown"
        reason = payload.get("reason") or "no reason given"
        guidance = payload.get("guidance") or ""
        conf = payload.get("confidence")
        lines = [f"**Rejected:** `{original}`", f"*{reason}*"]
        if guidance:
            lines.append(f"→ {guidance}")
        if conf is not None:
            lines.append(f"_confidence {conf}_")
        return "\n".join(lines), None
    if payload.get("type") == "arc_approval":
        original = payload.get("original_type") or "?"
        content = f"**Approved:** `{original}`"
        if payload.get("reason"):
            content += f"\n_{payload['reason']}_"
        return content, None
    if payload.get("type") in ("status_update", "heartbeat"):
        state = payload.get("status") or payload.get("state") or "?"
        action = payload.get("action") or payload.get("last_action") or ""
        return f"_{state}_" + (f" — {action}" if action else ""), None
    useful = {k: v for k, v in payload.items()
              if k not in ("ts", "timestamp", "id", "channel_id", "channel_type", "from_user")
              and not k.startswith("_") and v not in (None, "", [], {})}
    if 0 < len(useful) <= 3:
        return "\n".join(f"**{k}:** {v}" for k, v in useful.items()), None
    return None, None

# ──────────────────────────────────────────────────────────────────────
# Individual crew bot — one per crew member
# ──────────────────────────────────────────────────────────────────────
class CrewBot:
    """A single Discord client wearing one crew member's identity."""

    def __init__(self, bot_id: str, character: dict, token: str):
        self.bot_id = bot_id
        self.character = character
        self.token = token
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self._user_dm_channels: dict[int, discord.DMChannel] = {}  # user_id → DMChannel
        # Register events
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self):
        u = self.client.user
        log.info("[%s] online as %s (id=%s)", self.bot_id, u, u.id if u else "?")

    async def on_message(self, msg: discord.Message):
        if msg.author == self.client.user or msg.author.bot:
            return
        # Only respond to DMs — channel messages are the hub bot's job
        if not isinstance(msg.channel, discord.DMChannel):
            return

        # Cache this user's DM channel so we can post unsolicited messages later
        self._user_dm_channels[msg.author.id] = msg.channel

        text = msg.content.strip()
        log.info("[%s] DM from %s: %s", self.bot_id, msg.author, text[:80])

        # Special command: anything starting with /
        if text.startswith("/"):
            await self._handle_command(msg, text)
            return

        # Push to bus addressed to THIS crew member
        bus.push(
            sender="discord",
            recipient=self.bot_id,
            payload={
                "type": "discord_message",
                "from_user": str(msg.author),
                "from_user_id": msg.author.id,
                "channel_id": msg.channel.id,
                "channel_type": "dm",
                "content": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

        # Quick ack with the crew member's action dialogue line
        try:
            ack_line = pick_response_line(self.bot_id, "action")
            await msg.channel.send(f"_{ack_line}_")
        except Exception as e:
            log.warning("[%s] ack failed: %s", self.bot_id, e)

    async def _handle_command(self, msg: discord.Message, text: str):
        """Slash-style commands as plain text in DM."""
        lower = text.lower()
        if lower == "/who":
            await msg.channel.send(
                f"I'm **{self.character.get('name', self.bot_id)}** — "
                f"{self.character.get('role', '?')}.  "
                f"_{self.character.get('catchphrase', '')}_"
            )
        elif lower.startswith("/log"):
            note = text[4:].strip()
            session = getattr(self.client, "_aio_session", None)
            if session and note:
                oid = await write_ignition(session, {
                    "surface": f"discord_dm_{self.bot_id}",
                    "action_taken": "log_moment",
                    "arc_approved": True,
                    "notes": f"[{msg.author}] {note}",
                })
                await msg.channel.send(
                    f"Logged · `{(oid or '????')[:8]}`" if oid else "Log failed."
                )
            else:
                await msg.channel.send("Usage: `/log <text>`")
        elif lower.startswith("/godmode"):
            note = text.replace("/godmode", "", 1).strip() or f"Ignited via DM with {self.character.get('name', self.bot_id)}"
            session = getattr(self.client, "_aio_session", None)
            if session:
                oid = await write_ignition(session, {
                    "surface": f"discord_dm_{self.bot_id}",
                    "action_taken": "godmode_ignite",
                    "arc_approved": True,
                    "notes": note,
                })
                await msg.channel.send(
                    f"🔥 GOD MODE · `{(oid or '????')[:8]}`"
                )
        else:
            await msg.channel.send(f"_{self.character.get('catchphrase', 'Acknowledged.')}_")

    async def deliver_to_user(self, user_id: int, content: str, embed: dict | None = None):
        """Post a message in a user's DM with this bot. Opens DM channel if needed."""
        channel = self._user_dm_channels.get(user_id)
        if not channel:
            try:
                user = await self.client.fetch_user(user_id)
                channel = await user.create_dm()
                self._user_dm_channels[user_id] = channel
            except Exception as e:
                log.warning("[%s] cannot open DM with user %s: %s", self.bot_id, user_id, e)
                return False
        try:
            if embed:
                e = discord.Embed.from_dict(embed)
                await channel.send(content=content[:1900], embed=e)
            else:
                await channel.send(content[:1900])
            return True
        except Exception as e:
            log.warning("[%s] DM send failed: %s", self.bot_id, e)
            return False

    async def start(self):
        await self.client.start(self.token)

    @property
    def user_id(self) -> int | None:
        return self.client.user.id if self.client.user else None

# ──────────────────────────────────────────────────────────────────────
# Hub bot — keeps the old Path-A channel webhook flow alive
# ──────────────────────────────────────────────────────────────────────
class HubBot:
    """The original 'MEGA Crew Bridge' bot — channel mentions + webhook posts."""

    def __init__(self, token: str):
        self.token = token
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        self.client = discord.Client(intents=intents)
        self.client.event(self.on_ready)
        self.client.event(self.on_message)

    async def on_ready(self):
        log.info("[hub] online as %s", self.client.user)

    async def on_message(self, msg: discord.Message):
        if msg.author == self.client.user or msg.author.bot:
            return
        # Hub only handles channel @mentions; individual bots own DMs
        if isinstance(msg.channel, discord.DMChannel):
            await msg.channel.send(
                "_(This is the hub bot. To talk one-on-one, DM the crew member directly — "
                "Arc, Sparky, Weld, etc. show up in your DM list as separate people.)_"
            )
            return
        if self.client.user not in msg.mentions:
            return
        # Strip the mention and route
        text = msg.content
        for m in msg.mentions:
            text = text.replace(f"<@{m.id}>", "").replace(f"<@!{m.id}>", "")
        text = text.strip()
        bus.push(
            sender="discord",
            recipient="arc",  # broadcasts go to Arc first
            payload={
                "type": "discord_message",
                "from_user": str(msg.author),
                "from_user_id": msg.author.id,
                "channel_id": msg.channel.id,
                "channel_type": "guild",
                "content": text,
                "ts": datetime.now(timezone.utc).isoformat(),
            },
        )

    async def start(self):
        await self.client.start(self.token)

# ──────────────────────────────────────────────────────────────────────
# Bus drain — fans messages out to the right crew bot's DM
# ──────────────────────────────────────────────────────────────────────
async def drain_bus(crew: dict[str, CrewBot]):
    log.info("Bus drain loop started (interval=%ss) — %d crew bots", POLL_INTERVAL_SEC, len(crew))
    while True:
        try:
            # We pull for "discord" (legacy hub recipient) AND for each crew member
            recipients = ["discord"] + list(crew.keys())
            posted = 0
            for r in recipients:
                messages = await asyncio.to_thread(bus.pull, r, 20)
                for m in messages:
                    payload = m.get("payload", {})
                    sender = m.get("sender", r)
                    content, embed = format_payload(
                        payload if isinstance(payload, dict) else {},
                        sender,
                    )
                    if not content:
                        continue
                    # If addressed to a specific crew member, deliver via that crew member's bot
                    if r in crew:
                        user_id = payload.get("from_user_id") if isinstance(payload, dict) else None
                        # If no user_id in payload, this is an unsolicited broadcast; skip silently
                        if user_id:
                            ok = await crew[r].deliver_to_user(user_id, content, embed)
                            if ok:
                                posted += 1
            write_status("OK", f"drained posted={posted}")
        except Exception as e:
            log.error("drain loop error: %s", e)
            write_status("ERROR", str(e)[:120])
        await asyncio.sleep(POLL_INTERVAL_SEC)

# ──────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────
async def main():
    if not CHARACTERS:
        log.error("Empty character roster. characters.json missing or unreadable.")
        sys.exit(1)

    # Build the crew — one CrewBot per crew member with a configured token
    crew: dict[str, CrewBot] = {}
    skipped = []
    for bot_id, character in CHARACTERS.items():
        token = os.environ.get(token_env_for(bot_id), "").strip()
        if not token:
            skipped.append(bot_id)
            continue
        crew[bot_id] = CrewBot(bot_id, character, token)

    if not crew and not HUB_TOKEN:
        log.error("No bot tokens configured. Set DISCORD_TOKEN or DISCORD_TOKEN_<NAME>= for at least one bot.")
        sys.exit(1)

    log.info("Crew bots configured: %d  ·  Skipped (no token): %d", len(crew), len(skipped))
    if skipped:
        log.info("Skipped: %s", ", ".join(skipped))

    # Shared aiohttp session (attached to each bot's client for command handlers)
    session = aiohttp.ClientSession()
    for cb in crew.values():
        cb.client._aio_session = session

    # Build the coroutine list
    coros = [cb.start() for cb in crew.values()]
    if HUB_TOKEN:
        hub = HubBot(HUB_TOKEN)
        coros.append(hub.start())
    coros.append(drain_bus(crew))

    write_status("STARTING", f"crew={len(crew)} hub={'on' if HUB_TOKEN else 'off'}")
    try:
        await asyncio.gather(*coros)
    finally:
        await session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Interrupted.")
