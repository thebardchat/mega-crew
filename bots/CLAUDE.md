# CLAUDE.md — MEGA Crew Bots Development Context

> Updated: 2026-04-09 | 17 autonomous bots in Docker containers

## Architecture

```
mega/bots/
├── bot_base.py          # BaseBot class — all 17 inherit from this
├── bus.py               # SQLite message bus (MEGA_BASE/bus.db)
├── mega_client.py       # Ollama client wrapper
├── weaviate_client.py   # Weaviate connection helper
├── _knowledge_template.py  # BotKnowledge base class
├── crew_supervisor.py   # Orchestrator (--docker or subprocess mode)
├── docker-compose.yml   # All 17 services, shared volumes, host network
├── Dockerfile.base      # Python 3.13-slim + deps + shared modules
├── requirements.txt     # Shared Python dependencies
└── {name}/              # Per-bot directory (×17)
    ├── bot.py           # Main bot logic, tick() method
    ├── knowledge.py     # Domain-specific Weaviate queries
    ├── config.json      # Role, zone, interval, parameters
    ├── Dockerfile       # FROM mega-crew-base:latest + bot files
    └── backups/         # Auto-created by Weld on code changes

## The 17 Bots

| Bot | Zone | Role |
|-----|------|------|
| sparky | Brain | Training data judge — mines quality examples |
| volt | Brain | Drift detector — monitors persona consistency |
| neon | Brain | Weaviate embedder — manages vector store |
| glitch | Brain | Adversarial tester — stress-tests other bots |
| rivet | Left Hand | Dedup + format — cleans training data |
| torch | Left Hand | Persona editor — proposes prompt improvements |
| weld | Left Hand | Change applier — writes files, restarts containers |
| blaze | Right Hand | Context injector — pulls daily notes + voice dumps |
| arc | Right Hand | Gatekeeper — reviews ALL proposals before apply |
| flux | Right Hand | Health monitor — checks services + resources |
| gemini_strategist | Right Hand | Growth coach — Gemini 2.5 Flash, 4x/day |
| bolt | Left Foot | Log pattern analyst |
| stomp | Left Foot | Memory conflict resolver |
| grind | Left Foot | Bulk re-embedder |
| crank | Right Foot | Scheduler — coordinates timing |
| spike | Right Foot | IQ benchmarker — tests model quality |
| forge | Right Foot | Tool stub drafter |

## Key Patterns

### Path Abstraction
All bots use `MEGA_BASE` env var for paths:
```python
_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
```
- Host: defaults to real path
- Docker: set to `/mega` via docker-compose environment

### Message Bus Flow
```
Bot → push_to_bus("arc", payload) → Arc reviews → push_to_bus("weld", approved) → Weld applies
```
Message types: `training_proposal`, `persona_update`, `instruction_update`, `code_proposal`

### Self-Modification Pipeline
1. Bot calls `self.propose_code_change(new_code, rationale, summary)`
2. BaseBot generates unified diff, sends `code_proposal` to Arc via bus
3. Arc checks: self-only (source == target), CODE_BLACKLIST, compile(), confidence >= 0.75
4. If approved, Weld: backup current → write new → `docker restart mega-{name}`

### Per-Bot Memory (Weaviate)
- Collection: `BotMemory`, filtered by `bot_name`
- `self.remember(content, memory_type)` — store learning
- `self.recall(query, limit)` — semantic search own memories
- `self.recall_all()` — get everything
- Memories injected into system prompt via `build_system_prompt()`

### Knowledge Layer
Each bot has `knowledge.py` inheriting `BotKnowledge`:
- Override `get_domain_context()` for domain-specific Weaviate queries
- Accessed via `self.knowledge` in bot.py

## Docker Operations

```bash
# Build all (from mega/bots/)
docker compose build

# Start all
docker compose up -d

# Logs for one bot
docker compose logs -f sparky

# Restart one bot
docker restart mega-sparky

# Full rebuild after changing shared modules
docker build -t mega-crew-base:latest -f Dockerfile.base .
docker compose build --no-cache
docker compose up -d
```

## Safety Rules

- Arc is the ONLY reviewer — never bypass
- CODE_BLACKLIST: eval, exec, os.system, subprocess.call, __import__('os'), rm -rf, shutil.rmtree
- Bots can ONLY modify their own bot.py (self-only enforcement)
- Weld runs compile() AGAIN before writing (double-check)
- Backups created before every code write
- LLM routing: llama3.2:1b via localhost:11434 ONLY (not cluster proxy)
```
