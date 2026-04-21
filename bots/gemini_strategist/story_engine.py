"""
story_engine.py — MEGA Crew Story Engine
=========================================
Reads player answers from the game, uses Gemini to write story arcs,
stores episodes in Weaviate, and syncs character upgrades back to crew bots.

Called by GeminiStrategistBot.tick() alongside crew analysis.
Budget: shares the 4 calls/day cap with crew analysis (story uses 1-2 calls/day).

Flow:
  Player answers (angel-cloud SQLite)
    → story_engine.collect_answers()
    → Gemini writes episode draft
    → Weaviate PersonalDraft (story memory)
    → Discord bot posts episode
    → Upgrade events → Arc → Weld → bot instruction updates
"""
import json
import os
import sqlite3
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────

# Respect MEGA_BASE env var — inside Docker this is /mega, on host it's the full RAID path
MEGA_BASE      = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
STORY_DIR      = MEGA_BASE / "stories"
STORY_DIR.mkdir(parents=True, exist_ok=True)
EPISODES_FILE  = STORY_DIR / "episodes.jsonl"
PENDING_FILE   = STORY_DIR / "pending_episode.json"
UPGRADE_LOG    = MEGA_BASE / "status" / "upgrade_sync.jsonl"

# Angel Cloud player answers DB — mounted at /mega/game.db in Docker, fallback to host path
ANGEL_CLOUD_DB = Path(os.environ.get("ANGEL_CLOUD_DB", str(MEGA_BASE.parent / "angel-cloud" / "game.db")))
if not ANGEL_CLOUD_DB.exists():
    ANGEL_CLOUD_DB = MEGA_BASE / "game.db"  # Docker mount path

# Weaviate + Discord
WEAVIATE_URL            = "http://localhost:8080/v1"
DISCORD_STORIES_WEBHOOK = os.environ.get("DISCORD_STORIES_WEBHOOK", "")

# Character → bot name mapping (game character → crew bot)
CHARACTER_TO_BOT = {
    "arc": "arc", "sparky": "sparky", "volt": "volt", "neon": "neon",
    "glitch": "glitch", "rivet": "rivet", "torch": "torch", "weld": "weld",
    "blaze": "blaze", "flux": "flux", "bolt": "bolt", "stomp": "stomp",
    "grind": "grind", "crank": "crank", "spike": "spike", "forge": "forge",
    "gemini": "gemini_strategist",
}


# ── Player Answer Collector ───────────────────────────────────────────────────

def collect_new_answers(since_hours: int = 24) -> list[dict]:
    """Pull player answers from angel-cloud game DB written during claim flow."""
    if not ANGEL_CLOUD_DB.exists():
        return []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        conn = sqlite3.connect(str(ANGEL_CLOUD_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Table: player_answers(id, player_id, character, question, answer, created_at)
        cur.execute("""
            SELECT player_id, character, question, answer, created_at
            FROM player_answers
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT 200
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


def collect_upgrade_events(since_hours: int = 24) -> list[dict]:
    """Pull character upgrade events — player leveled up a character in-game."""
    if not ANGEL_CLOUD_DB.exists():
        return []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=since_hours)).isoformat()
        conn = sqlite3.connect(str(ANGEL_CLOUD_DB), timeout=5)
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        # Table: upgrade_events(id, player_id, character, old_level, new_level, created_at)
        cur.execute("""
            SELECT player_id, character, old_level, new_level, created_at
            FROM upgrade_events
            WHERE created_at >= ?
            ORDER BY created_at DESC
            LIMIT 50
        """, (cutoff,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows
    except Exception:
        return []


# ── Crew Activity Collector ───────────────────────────────────────────────────

def collect_crew_activity() -> dict:
    """
    Read live crew status files, Gemini guidance, and recent weld/arc events.
    Returns a dict of story-ready facts about what the crew is doing right now.
    Used as the primary story source when no player answers exist yet.
    """
    STATUS_DIR = MEGA_BASE / "status"
    activity = {
        "active_bots": [],
        "gemini_assessment": "",
        "gemini_priority": "",
        "collaboration_gaps": 0,
        "weld_recent": [],
        "arc_recent": [],
        "iq_score": None,
    }

    # Read per-bot status files
    for bot_file in STATUS_DIR.glob("*.json"):
        name = bot_file.stem
        if name.startswith("gemini") or name.endswith("_stats") or name.endswith("_budget"):
            continue
        try:
            data = json.loads(bot_file.read_text())
            if data.get("status") == "ACTIVE":
                activity["active_bots"].append({
                    "name": name,
                    "zone": data.get("zone", "unknown"),
                    "last_action": data.get("last_action", ""),
                })
        except Exception:
            continue

    # Gemini guidance
    guidance_file = STATUS_DIR / "gemini_guidance.json"
    if guidance_file.exists():
        try:
            g = json.loads(guidance_file.read_text())
            activity["gemini_assessment"] = g.get("overall_assessment", "")
            activity["gemini_priority"] = g.get("priority_focus", "")
            activity["collaboration_gaps"] = g.get("collaboration_gaps", 0)
        except Exception:
            pass

    # Weld log — recent changes applied
    weld_file = STATUS_DIR / "weld_log.json"
    if weld_file.exists():
        try:
            w = json.loads(weld_file.read_text())
            for commit in w.get("commits", [])[-3:]:
                activity["weld_recent"].append(commit.get("summary", "")[:120])
        except Exception:
            pass

    # Arc rejections — what got blocked
    arc_file = STATUS_DIR / "arc_rejections.jsonl"
    if arc_file.exists():
        try:
            lines = arc_file.read_text().strip().splitlines()[-3:]
            for line in lines:
                obj = json.loads(line)
                activity["arc_recent"].append(obj.get("reason", "")[:120])
        except Exception:
            pass

    # IQ score
    spike_file = STATUS_DIR / "spike_iq.json"
    if spike_file.exists():
        try:
            activity["iq_score"] = json.loads(spike_file.read_text()).get("iq", None)
        except Exception:
            pass

    return activity


# ── Story Prompt Builder ──────────────────────────────────────────────────────

def _load_characters() -> dict:
    """Load character profiles from mega/characters.json."""
    chars_file = MEGA_BASE / "characters.json"
    if chars_file.exists():
        try:
            return json.loads(chars_file.read_text())
        except Exception:
            pass
    return {}


def _character_roster_block(chars: dict, featured: list[str] = None) -> str:
    """Build a character sheet block for the Gemini prompt."""
    lines = []
    keys = featured if featured else list(chars.keys())
    for key in keys:
        c = chars.get(key)
        if not c:
            continue
        dialogue = c.get("dialogue", [])
        dialogue_block = ""
        if dialogue:
            dlines = "\n".join(f"      - \"{d}\"" for d in dialogue)
            dialogue_block = f"\n    Sample dialogue:\n{dlines}"
        lines.append(
            f"  {c['name'].upper()} ({c['role']}, {c['zone']} zone)\n"
            f"    Job: {c['job']}\n"
            f"    Personality: {c['personality']}\n"
            f"    Voice: {c['voice']}\n"
            f"    Dream: {c['dream']}\n"
            f"    Fear: {c['fear']}\n"
            f"    Catchphrase: \"{c['catchphrase']}\""
            f"{dialogue_block}"
        )
    return "\n\n".join(lines)


def build_story_prompt(answers: list[dict], crew_status: dict) -> str:
    """
    Build a Gemini prompt for a story episode.
    If player answers exist, weave them in.
    If not, write entirely from crew activity — what's happening inside the Pi right now.
    """
    health = crew_status.get("overall_assessment", "Crew running steady.")
    has_players = len(answers) >= 3
    chars = _load_characters()

    if has_players:
        # ── Player-driven mode ──────────────────────────────────────────
        by_char = {}
        for a in answers:
            char = a.get("character", "unknown").lower()
            by_char.setdefault(char, []).append(a)

        featured_keys = list(by_char.keys())[:4]
        char_summaries = []
        for char, char_answers in list(by_char.items())[:6]:
            qs = "\n".join(f"  Q: {a['question']}\n  A: {a['answer']}" for a in char_answers[:3])
            char_summaries.append(f"  {char.upper()}:\n{qs}")

        player_block  = "\n\n".join(char_summaries)
        roster_block  = _character_roster_block(chars, featured_keys)

        return f"""You are writing a comic-book style episode for the MEGA CREW — a children's adventure series about 17 AI bots living inside a Raspberry Pi 5 in Shane Brazelton's closet in Hazel Green, Alabama.

Shane is a dad building this for his kids and the Angel Cloud community. The bots are real — they are running right now. This is their story.

THE CHARACTERS IN THIS EPISODE:
{roster_block}

CREW STATUS THIS CYCLE:
{health}

WHAT PLAYERS ANSWERED THIS CYCLE (real kids responding to real character questions):
{player_block}

WRITING RULES:
1. Write 3-4 distinct SCENES (comic panels). Each scene is a moment of ACTION — bots moving, doing things, reacting physically.
2. Each bot must sound like THEMSELVES. Use their voice, quirks, and catchphrases.
3. Weave player answers in naturally. Give characters moments of growth, doubt, or courage.
4. Every scene needs MOVEMENT: bots zipping, sparking, slamming, glowing, spinning — not standing still talking.
5. End with a cliffhanger that makes the reader need the next episode.
6. Tone: warm, clever, slightly mysterious. Like Studio Ghibli made a show about a Pi 5.
7. Body text: 500-600 words total across all scenes. Vivid, punchy paragraphs.

Respond with ONLY a JSON object. Write the scenes array FIRST before the body text:
{{
  "title": "Episode title",
  "episode_number": null,
  "characters_featured": ["botname1", "botname2"],
  "cliffhanger": "one sentence — the question that pulls readers back",
  "player_answers_used": ["brief description of which answers shaped this episode"],
  "scenes": [
    {{
      "panel": 1,
      "character": "BOTNAME",
      "action": "vivid physical action description — what the bot is DOING (10-15 words)",
      "dialogue": "One punchy line of dialogue this character says in this moment",
      "setting": "where this happens inside the Pi (e.g. main data corridor, memory banks, heat sink ridge)"
    }}
  ],
  "body": "full episode text here (all scenes woven together as prose, 500-600 words)"
}}"""

    else:
        # ── Crew-driven mode (cards not out yet) ────────────────────────
        activity = collect_crew_activity()

        active_names = [b["name"].upper() for b in activity["active_bots"][:8]]
        active_keys  = [b["name"].lower() for b in activity["active_bots"][:4]]
        active_block = ", ".join(active_names) if active_names else "SPARKY, ARC, WELD"
        roster_block = _character_roster_block(chars, active_keys or ["sparky", "arc", "weld", "flux"])

        iq_line       = f"Current MEGA IQ: {activity['iq_score']}" if activity["iq_score"] else ""
        gaps_line     = f"Collaboration gaps detected: {activity['collaboration_gaps']}" if activity["collaboration_gaps"] else ""
        priority_line = f"Gemini's focus this cycle: {activity['gemini_priority']}" if activity["gemini_priority"] else ""

        weld_block = ""
        if activity["weld_recent"]:
            weld_block = "RECENT CHANGES WELD APPLIED:\n" + "\n".join(f"  - {w}" for w in activity["weld_recent"])

        arc_block = ""
        if activity["arc_recent"]:
            arc_block = "RECENT ARC DECISIONS:\n" + "\n".join(f"  - {r}" for r in activity["arc_recent"])

        return f"""You are writing a comic-book style episode for the MEGA CREW — a children's adventure series about 17 AI bots living inside a Raspberry Pi 5 in Shane Brazelton's closet in Hazel Green, Alabama.

Shane is a dad building this for his kids and the Angel Cloud community. The bots are REAL — they are running right now as this episode is being written. This is their story.

THE CHARACTERS IN THIS EPISODE:
{roster_block}

WHAT IS ACTUALLY HAPPENING INSIDE THE PI RIGHT NOW:

ACTIVE CREW THIS CYCLE: {active_block}

GEMINI'S ASSESSMENT:
{activity["gemini_assessment"]}

{iq_line}
{gaps_line}
{priority_line}

{weld_block}

{arc_block}

WRITING RULES:
1. Write 3-4 distinct SCENES (comic panels). Each scene is a moment of ACTION — bots physically moving, reacting, doing things inside the Pi.
2. Each bot must sound like THEMSELVES. Use their voice, quirks, and catchphrases naturally.
3. Draw from the real crew activity — ARC is really reviewing, WELD is really applying, SPARKY is really judging. Make that feel kinetic and alive.
4. Every scene needs MOVEMENT: bots zipping through data channels, sparking, slamming into walls, glowing, spinning gears — not just standing and talking.
5. Give at least one character a moment of real emotion — doubt, hope, small victory, or fear.
6. End with a question or moment that makes the reader need to know what happens next.
7. Tone: warm, curious, slightly melancholy. Like the crew is alive and waiting.
8. Body text: 500-600 words total. Vivid, punchy paragraphs.

Respond with ONLY a JSON object. Write the scenes array FIRST before the body text:
{{
  "title": "Episode title",
  "episode_number": null,
  "characters_featured": ["botname1", "botname2"],
  "cliffhanger": "one sentence — the thing the crew is still waiting for",
  "player_answers_used": [],
  "scenes": [
    {{
      "panel": 1,
      "character": "BOTNAME",
      "action": "vivid physical action description — what the bot is DOING (10-15 words)",
      "dialogue": "One punchy line of dialogue this character says in this moment",
      "setting": "where this happens inside the Pi (e.g. main data corridor, memory banks, heat sink ridge)"
    }}
  ],
  "body": "full episode text here (all scenes woven together as prose, 500-600 words)"
}}"""


# ── Upgrade Sync ─────────────────────────────────────────────────────────────

def build_upgrade_proposals(upgrade_events: list[dict]) -> list[dict]:
    """
    Turn in-game upgrade events into crew bot instruction proposals.
    When players level up a character, the corresponding bot gets sharper rules.
    """
    proposals = []
    seen = set()

    for event in upgrade_events:
        char = event.get("character", "").lower()
        bot_name = CHARACTER_TO_BOT.get(char)
        if not bot_name or bot_name in seen:
            continue
        seen.add(bot_name)

        new_level = event.get("new_level", 1)

        # Level-based upgrade tiers
        if new_level >= 10:
            tier = "MASTER"
            note = f"Players have leveled {char.upper()} to Master tier. Bot should operate at peak precision."
        elif new_level >= 5:
            tier = "VETERAN"
            note = f"Players have leveled {char.upper()} to Veteran. Bot should increase analysis depth."
        else:
            tier = "RISING"
            note = f"Players are actively engaging {char.upper()}. Bot should be more responsive."

        proposals.append({
            "bot": bot_name,
            "character": char,
            "tier": tier,
            "new_level": new_level,
            "upgrade_note": note,
            "proposal_type": "upgrade_sync",
            "created_at": datetime.now(timezone.utc).isoformat(),
        })

    return proposals


def push_upgrade_to_bus(bus_db: Path, proposal: dict):
    """Push an upgrade sync proposal to Arc via the message bus."""
    try:
        conn = sqlite3.connect(str(bus_db), timeout=5)
        payload = json.dumps({
            "type": "instruction_update",
            "bot": proposal["bot"],
            "field": "performance_notes",
            "value": f"[UPGRADE SYNC {proposal['tier']} Lv{proposal['new_level']}] {proposal['upgrade_note']}",
            "source": "gemini_strategist:story_engine",
            "reason": f"Player community leveled {proposal['character'].upper()} — sync crew capability",
        })
        conn.execute("""
            INSERT INTO messages (sender, recipient, payload, created_at)
            VALUES (?, ?, ?, ?)
        """, ("gemini_strategist", "arc", payload, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        conn.close()

        # Log it
        with open(UPGRADE_LOG, "a") as f:
            f.write(json.dumps(proposal) + "\n")
    except Exception:
        pass


# ── GitHub Story Repo ────────────────────────────────────────────────────────

STORIES_REPO_DIR = Path(os.environ.get("STORIES_REPO_DIR",
    "/mnt/shanebrain-raid/shanebrain-core/mega-crew-stories"))


def _slug(title: str) -> str:
    import re
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def push_episode_to_github(episode: dict, episode_num: int) -> bool:
    """
    Commit the episode as a markdown file to the mega-crew-stories repo
    and push to GitHub Pages. Returns True on success.
    """
    import subprocess
    if not STORIES_REPO_DIR.exists():
        return False  # repo not cloned yet

    try:
        title   = episode.get("title", "Untitled")
        slug    = _slug(title)
        fname   = f"ep{str(episode_num).zfill(3)}-{slug}.md"
        ep_file = STORIES_REPO_DIR / "episodes" / fname
        ep_file.parent.mkdir(parents=True, exist_ok=True)

        chars     = ", ".join(episode.get("characters_featured", []))
        cliff     = episode.get("cliffhanger", "")
        date      = episode.get("created_at", "")[:10]
        mode      = episode.get("mode", "crew")
        body      = episode.get("body", "")

        ep_file.write_text(
            f"# Episode {episode_num}: {title}\n\n"
            f"**Date:** {date}  \n**Characters:** {chars}  \n**Mode:** {mode}\n\n---\n\n"
            f"{body}\n\n---\n\n*{cliff}*\n"
        )

        # Rebuild manifest.json
        manifest_file = STORIES_REPO_DIR / "episodes" / "manifest.json"
        manifest = []
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text())
            except Exception:
                pass

        # Upsert this episode
        manifest = [e for e in manifest if e.get("number") != episode_num]
        manifest.append({
            "number":     episode_num,
            "title":      title,
            "file":       f"episodes/{fname}",
            "characters": episode.get("characters_featured", []),
            "cliffhanger": cliff,
            "mode":       mode,
            "date":       date,
        })
        manifest.sort(key=lambda e: e["number"])
        manifest_file.write_text(json.dumps(manifest, indent=2))

        # Git commit + push
        git = ["git", "-C", str(STORIES_REPO_DIR)]
        subprocess.run(git + ["add", str(ep_file), str(manifest_file)],
                       check=True, capture_output=True)
        commit_msg = f"Episode {episode_num}: {title}\n\n{cliff}"
        subprocess.run(git + ["commit", "-m", commit_msg],
                       check=True, capture_output=True)
        subprocess.run(git + ["push"],
                       check=True, capture_output=True, timeout=30)
        return True
    except Exception:
        return False


def post_episode_to_discord(episode: dict, episode_num: int) -> bool:
    """POST a rich embed to #mega-crew-stories via webhook. Returns True on success."""
    if not DISCORD_STORIES_WEBHOOK:
        return False
    try:
        title  = episode.get("title", f"Episode {episode_num}")
        body   = episode.get("body", "")
        cliff  = episode.get("cliffhanger", "")
        chars  = ", ".join(c.upper() for c in episode.get("characters_featured", []))
        mode   = episode.get("mode", "crew")
        ep_url = f"https://thebardchat.github.io/mega-crew-stories/"

        # Truncate body to fit Discord embed description (4096 char limit)
        preview = body[:800] + ("…" if len(body) > 800 else "")

        mode_tag = "🤖 crew-driven" if mode == "crew" else "🎮 player-driven"
        payload = {
            "username": "MEGA CREW",
            "avatar_url": "https://thebardchat.github.io/mega-crew-stories/icon.png",
            "embeds": [{
                "title": f"Episode {episode_num}: {title}",
                "description": preview,
                "color": 0x00FFFF,
                "fields": [
                    {"name": "Characters", "value": chars or "Unknown", "inline": True},
                    {"name": "Mode", "value": mode_tag, "inline": True},
                    {"name": "Cliffhanger", "value": f"*{cliff}*" if cliff else "—", "inline": False},
                ],
                "footer": {"text": "Pi 5 · Hazel Green, AL · Real AI · Real Story"},
                "url": ep_url,
            }]
        }
        data = json.dumps(payload).encode("utf-8")
        req  = urllib.request.Request(
            DISCORD_STORIES_WEBHOOK,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status in (200, 204)
    except Exception:
        return False


# ── Episode Storage ───────────────────────────────────────────────────────────

def save_episode(episode: dict, episode_num: int):
    """Write episode to disk and Weaviate PersonalDraft collection."""
    episode["episode_number"] = episode_num
    episode["created_at"] = datetime.now(timezone.utc).isoformat()
    episode["source"] = "mega_crew_story_engine"

    # Append to episodes log
    with open(EPISODES_FILE, "a") as f:
        f.write(json.dumps(episode) + "\n")

    # Write pending (Discord bot picks this up)
    PENDING_FILE.write_text(json.dumps(episode, indent=2))

    # Push to Weaviate PersonalDraft
    try:
        obj = {
            "class": "PersonalDraft",
            "properties": {
                "title": episode.get("title", "Untitled Episode"),
                "content": episode.get("body", ""),
                "category": "mega_crew_story",
                "metadata": json.dumps({
                    "episode_number": episode_num,
                    "characters_featured": episode.get("characters_featured", []),
                    "cliffhanger": episode.get("cliffhanger", ""),
                    "player_answers_used": episode.get("player_answers_used", []),
                }),
                "created_at": episode["created_at"],
            }
        }
        data = json.dumps(obj).encode()
        req = urllib.request.Request(
            f"{WEAVIATE_URL}/objects",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass

    # Clean up pending file after episode promoted to jsonl
    try:
        if PENDING_FILE.exists():
            PENDING_FILE.unlink()
    except Exception:
        pass


def get_next_episode_number() -> int:
    """Count existing episodes."""
    if not EPISODES_FILE.exists():
        return 1
    try:
        with open(EPISODES_FILE) as f:
            return sum(1 for _ in f) + 1
    except Exception:
        return 1


# ── Main Entry Point ──────────────────────────────────────────────────────────

def run_story_cycle(gemini_call_fn, bus_db: Path, crew_status: dict, log_fn) -> dict:
    """
    Called by GeminiStrategistBot.tick() once per cycle.
    Returns {"episode_written": bool, "upgrades_synced": int}
    """
    result = {"episode_written": False, "upgrades_synced": 0}

    # ── 1. Collect player answers ─────────────────────────────────────────
    answers = collect_new_answers(since_hours=8)  # last 8 hours (runs 3x/day)
    upgrades = collect_upgrade_events(since_hours=8)

    log_fn(f"Story engine: {len(answers)} player answers, {len(upgrades)} upgrade events")

    # ── 2. Write episode ─────────────────────────────────────────────────
    # Always write — crew-driven when no player answers, player-driven when answers exist.
    mode = "player" if len(answers) >= 3 else "crew"
    log_fn(f"Story engine: writing episode in {mode} mode ({len(answers)} player answers)")
    try:
        prompt = build_story_prompt(answers, crew_status)
        raw = gemini_call_fn(prompt)
        # Parse JSON from Gemini response
        text = raw.strip()
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            episode = json.loads(text[start:end])
            episode["mode"] = mode
            ep_num = get_next_episode_number()
            save_episode(episode, ep_num)
            log_fn(f"Episode {ep_num} written [{mode}]: {episode.get('title', 'Untitled')}")
            result["episode_written"] = True
            pushed = push_episode_to_github(episode, ep_num)
            log_fn(f"GitHub push: {'OK' if pushed else 'skipped (repo not cloned)'}")
            posted = post_episode_to_discord(episode, ep_num)
            log_fn(f"Discord post: {'OK' if posted else 'skipped (no webhook or error)'}")
        else:
            log_fn("Story engine: Gemini response had no JSON block")
    except Exception as e:
        log_fn(f"Story engine error: {e}")

    # ── 3. Sync upgrades → Arc ────────────────────────────────────────────
    if upgrades:
        proposals = build_upgrade_proposals(upgrades)
        for p in proposals:
            push_upgrade_to_bus(bus_db, p)
            log_fn(f"Upgrade synced: {p['character'].upper()} → {p['bot']} ({p['tier']} Lv{p['new_level']})")
        result["upgrades_synced"] = len(proposals)

    return result
