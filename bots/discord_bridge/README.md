# discord_bridge — MEGA Crew's voice on Discord

> One Discord application. 17 faces. Same bus underneath.

This bridge is **not** a chatbot. It's the I/O layer that lets your existing MEGA Crew bots show up on Discord wearing their own names and avatars. Every DM you send routes into `bus.push()`. Every reply the crew pushes to `recipient="discord"` gets posted via the right webhook.

## Setup — 6 Steps

### 1. Create the Discord application (once)

1. Go to https://discord.com/developers/applications → New Application → name it `MEGA Crew Bridge`
2. Bot tab → Add Bot → copy the **Token** → save in vault: `shanebrain_vault_add` category=api_keys name=discord_bot_token
3. Bot tab → enable **MESSAGE CONTENT INTENT** (required for reading DM content)
4. OAuth2 → URL Generator → scopes: `bot`, `applications.commands` → permissions: Send Messages, Read Message History, Manage Webhooks → copy URL, open it in browser, add bot to your private Discord server

### 2. Create 17 webhooks (one per crew member)

In your private Discord server, create a channel called `#mega-crew-chatter`. Then for each crew member, run **Channel Settings → Integrations → Webhooks → New Webhook**:

- Name the webhook with the crew member's name (e.g., `Arc`)
- Optional: upload their `cards/mega_front/{name}_mega_front.png` as the avatar (the bridge also passes `avatar_url` per-post which overrides this)
- Copy the webhook URL
- Save to vault: `shanebrain_vault_add` category=webhooks name=webhook_arc

Repeat for all 17. Vault names use the pattern `webhook_<bot_id>`:
`webhook_sparky`, `webhook_arc`, `webhook_weld`, `webhook_blaze`, `webhook_volt`, `webhook_neon`, `webhook_glitch`, `webhook_rivet`, `webhook_torch`, `webhook_flux`, `webhook_bolt`, `webhook_stomp`, `webhook_grind`, `webhook_crank`, `webhook_spike`, `webhook_forge`, `webhook_gemini_strategist`

### 3. Add env vars to docker-compose.yml

Append this service to `bots/docker-compose.yml`:

```yaml
  discord_bridge:
    <<: *bot-common
    build:
      context: .
      dockerfile: discord_bridge/Dockerfile
    container_name: mega-discord-bridge
    environment:
      - PYTHONUNBUFFERED=1
      - MEGA_BASE=/mega
      - DISCORD_TOKEN=${DISCORD_TOKEN}
      - WEAVIATE_URL=http://100.100.90.66:8080
      - WEBHOOK_SPARKY=${WEBHOOK_SPARKY}
      - WEBHOOK_ARC=${WEBHOOK_ARC}
      - WEBHOOK_WELD=${WEBHOOK_WELD}
      - WEBHOOK_BLAZE=${WEBHOOK_BLAZE}
      - WEBHOOK_VOLT=${WEBHOOK_VOLT}
      - WEBHOOK_NEON=${WEBHOOK_NEON}
      - WEBHOOK_GLITCH=${WEBHOOK_GLITCH}
      - WEBHOOK_RIVET=${WEBHOOK_RIVET}
      - WEBHOOK_TORCH=${WEBHOOK_TORCH}
      - WEBHOOK_FLUX=${WEBHOOK_FLUX}
      - WEBHOOK_BOLT=${WEBHOOK_BOLT}
      - WEBHOOK_STOMP=${WEBHOOK_STOMP}
      - WEBHOOK_GRIND=${WEBHOOK_GRIND}
      - WEBHOOK_CRANK=${WEBHOOK_CRANK}
      - WEBHOOK_SPIKE=${WEBHOOK_SPIKE}
      - WEBHOOK_FORGE=${WEBHOOK_FORGE}
      - WEBHOOK_GEMINI_STRATEGIST=${WEBHOOK_GEMINI_STRATEGIST}
```

### 4. Create `.env` next to docker-compose.yml on shanebrain Pi

```env
DISCORD_TOKEN=<paste from vault>
WEBHOOK_SPARKY=<paste from vault>
WEBHOOK_ARC=<paste from vault>
... (all 17)
```

`.gitignore` should include `.env` so you never commit secrets.

### 5. Build + run

```bash
cd /mnt/shanebrain-raid/shanebrain-core/mega/bots
docker compose build discord_bridge
docker compose up -d discord_bridge
docker compose logs -f discord_bridge
```

You should see `Connected as MEGA Crew Bridge#1234 — 17 crew members loaded`.

### 6. Test it

In Discord, DM your bot (or @mention it in a server channel):

| Type | What happens |
|------|--------------|
| `/who` | Lists all 17 crew + roles |
| `arc check the queue` | Routes to Arc via bus, Arc acks in chat with his action dialogue |
| `volt scan drift` | Routes to Volt |
| `gemini six months` | Routes to Gemini Strategist |
| `/log Bridgeport ready for Monday` | Writes IgnitionEvent with notes |
| `/mood flow` | Writes IgnitionEvent with mood=flow |
| `/godmode` | Fires the ignition ritual into Weaviate |
| `/recent` | Last 5 IgnitionEvents from Weaviate |

## How Routing Works

**Inbound** (Discord → Crew):
1. User DMs the bot
2. Bridge parses first word → matches a crew id or name
3. `bus.push(sender="discord", recipient="<bot>", payload={...})`
4. Bridge sends an ack via the recipient's webhook using their `dialogue[0]` line
5. The actual crew member bot picks up the message on its next interval (`bus.pull("<bot>")`)

**Outbound** (Crew → Discord):
1. Any crew bot does `bus.push(sender="<self>", recipient="discord", payload={"content": "..."})`
2. Every 2 seconds, bridge calls `bus.pull("discord")`
3. For each message, POSTs to the sender's webhook with their name + avatar

## Why This Architecture

- **Arc still gatekeeps.** Discord is just a new I/O channel into the existing approval flow. Arc reviews crew responses before they go out if Shane configures him to.
- **One Discord token.** No need for 17 separate bot applications. Webhooks let one app speak as many identities.
- **Mobile leverage.** Shane DMs the bot from his truck. Arc/Volt/Crank/etc. all respond in-character.
- **No new Weaviate schema.** Existing `IgnitionEvent` class handles all Discord-originated events with `surface="discord"`.

## What Each Crew Member Says By Default

Until you wire actual reasoning into each bot's responder (e.g. via Gemini API for the strategist), each one acks with a line from their `dialogue[]` array in `characters.json`. The bridge picks `dialogue[0]` (action) for acks.

Example: when you DM "arc check the queue", Arc replies "_Show me why this is better than what we already have._" — his line, his voice.

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Bot offline | Check `docker compose logs discord_bridge` |
| Slash commands missing | First sync can take up to 1 hour to propagate globally |
| Can't read DM content | Re-check **MESSAGE CONTENT INTENT** in Discord Developer Portal |
| Webhook not posting | Missing `WEBHOOK_<NAME>` env var — bridge falls back to bot's own message |
| Crew member never responds | Their bot container needs to pull from bus — check `docker compose logs <bot>` |

## Linked Memory
- [[god-mode]] — Discord is one of the six surfaces
- [[mega-crew-architecture]] — full bus + bot structure
- [[feedback-mega-crew-discord]] — why this is plural, not singular
