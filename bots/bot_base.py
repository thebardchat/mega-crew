"""
bot_base.py — BaseBot class. All 16 MEGA crew bots inherit this.
Each bot writes its own status file to /mega/status/{name}.json.
crew_supervisor aggregates them into bot_status.json.
"""
import json
import logging
import logging.handlers
import hashlib
import time
from abc import abstractmethod
from datetime import datetime
from pathlib import Path

import bus

import os
BASE_DIR       = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
STATUS_DIR     = BASE_DIR / "status"
LOGS_DIR       = BASE_DIR / "logs"
INSTRUCTIONS_DIR = BASE_DIR / "instructions"
STATUS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)
INSTRUCTIONS_DIR.mkdir(exist_ok=True)

# All known bot intervals (seconds) — used by Flux for heartbeat checks
BOT_INTERVALS = {
    "sparky":  600,
    "volt":    900,
    "neon":    300,
    "glitch":  1800,
    "rivet":   120,
    "torch":   180,
    "weld":    3600,
    "blaze":   1200,
    "arc":     30,
    "flux":    300,
    "bolt":    600,
    "stomp":   3600,
    "grind":   1800,
    "crank":   60,
    "spike":   21600,
    "forge":   86400,
}


class BaseBot:
    def __init__(self, name: str, zone: str, interval: int):
        self.name     = name
        self.zone     = zone
        self.interval = interval if name == "arc" else max(interval, 1800)
        self._status  = {
            "status":      "STARTING",
            "last_run":    None,
            "last_action": "Initializing",
            "zone":        zone,
            "interval_seconds": interval,
            "next_run":    None,
        }
        self._log = self._build_logger()
        self.instructions = self._load_instructions()
        self._write_status("STARTING", "Initializing")

    def _load_instructions(self) -> dict:
        """Load this bot's instruction file. Returns empty dict if not found."""
        path = INSTRUCTIONS_DIR / f"{self.name}.json"
        if path.exists():
            try:
                return json.loads(path.read_text())
            except Exception:
                pass
        return {}

    def reload_instructions(self):
        """Hot-reload instructions mid-run (called after Weld applies updates)."""
        self.instructions = self._load_instructions()
        self.log(f"Instructions reloaded — version {self.instructions.get('version', '?')}")

    def propose_instruction_update(self, field: str, new_value, reason: str):
        """Push an instruction update proposal to Arc for review."""
        self.push_to_bus("arc", {
            "type": "instruction_update_proposal",
            "source": self.name,
            "target_bot": self.name,
            "field": field,
            "new_value": new_value,
            "reason": reason,
            "current_version": self.instructions.get("version", 1),
        })
        self.log(f"Proposed instruction update: {field} → {reason[:60]}")

    # ── Instruction-Aware Prompting ─────────────────────────────────────────

    def build_system_prompt(self, base_prompt: str) -> str:
        """Inject live instruction rules AND learned memories into LLM system prompt.
        This is what makes bots actually evolve — updated rules + memories change behavior."""
        self.instructions = self._load_instructions()  # hot-reload every tick
        rules = self.instructions.get("rules", [])
        perf_notes = self.instructions.get("performance_notes", [])
        guidance = self.instructions.get("gemini_guidance", "")
        rejections = self.get_recent_rejections(3)

        extras = []
        if rules:
            extras.append("YOUR CURRENT RULES (follow these strictly):\n" +
                          "\n".join(f"  - {r}" for r in rules))
        if perf_notes:
            extras.append("PERFORMANCE NOTES:\n" +
                          "\n".join(f"  - {n}" for n in perf_notes[-5:]))
        if guidance:
            extras.append(f"GEMINI COACHING: {guidance}")
        if rejections:
            rej_lines = []
            for r in rejections:
                rej_lines.append(f"  - {r.get('type','?')}: {r.get('reason','')[:80]}")
            extras.append("RECENT REJECTIONS (avoid these mistakes):\n" + "\n".join(rej_lines))

        # Pull learned memories from Weaviate (patterns and learnings)
        try:
            memories = self.recall_all(limit=5)
            if memories:
                mem_lines = []
                for m in memories:
                    mem_lines.append(f"  - [{m.get('memory_type','?')}] {m.get('content','')[:100]}")
                extras.append("YOUR LEARNED MEMORIES:\n" + "\n".join(mem_lines))
        except Exception:
            pass  # don't break prompt building if Weaviate is down

        if extras:
            return base_prompt + "\n\n" + "\n\n".join(extras)
        return base_prompt

    # ── Logger ────────────────────────────────────────────────────────────────

    def _build_logger(self) -> logging.Logger:
        log = logging.getLogger(self.name)
        log.setLevel(logging.INFO)
        if not log.handlers:
            handler = logging.handlers.RotatingFileHandler(
                LOGS_DIR / f"{self.name}.log",
                maxBytes=10 * 1024 * 1024,
                backupCount=3,
            )
            handler.setFormatter(
                logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
            )
            log.addHandler(handler)
        return log

    def log(self, msg: str, level: str = "info"):
        getattr(self._log, level)(msg)

    # ── Status ────────────────────────────────────────────────────────────────

    def _write_status(self, status: str, action: str):
        now = datetime.utcnow().isoformat()
        next_run = datetime.utcfromtimestamp(
            time.time() + self.interval
        ).isoformat()
        self._status.update({
            "status":      status,
            "last_run":    now,
            "last_action": action,
            "next_run":    next_run,
        })
        path = STATUS_DIR / f"{self.name}.json"
        path.write_text(json.dumps(self._status, indent=2))

    def heartbeat(self, action: str = "Running"):
        self._write_status("ACTIVE", action)

    # ── Weaviate Memory (per-bot learning) ──────────────────────────────────

    WEAVIATE_URL = "http://localhost:8080"

    def remember(self, content: str, memory_type: str = "learning",
                 context: str = "", outcome: str = ""):
        """Store a memory in this bot's Weaviate knowledge space.
        memory_type: learning, rejection, pattern, decision, observation"""
        import urllib.request as _req
        obj = {
            "class": "BotMemory",
            "properties": {
                "bot_name":    self.name,
                "memory_type": memory_type,
                "content":     content[:1000],
                "context":     context[:500],
                "outcome":     outcome[:200],
                "timestamp":   datetime.utcnow().isoformat(),
            }
        }
        try:
            data = json.dumps(obj).encode()
            req = _req.Request(
                f"{self.WEAVIATE_URL}/v1/objects",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=10) as resp:
                if resp.status in (200, 201):
                    self.log(f"Remembered [{memory_type}]: {content[:60]}")
                    return True
        except Exception as e:
            self.log(f"Remember failed: {e}", "error")
        return False

    def recall(self, query: str, limit: int = 5, memory_type: str = None) -> list[dict]:
        """Search this bot's memories by semantic similarity.
        Returns list of {content, memory_type, context, outcome, timestamp}."""
        import urllib.request as _req
        where_filter = {
            "operator": "And",
            "operands": [
                {"path": ["bot_name"], "operator": "Equal", "valueText": self.name}
            ]
        }
        if memory_type:
            where_filter["operands"].append(
                {"path": ["memory_type"], "operator": "Equal", "valueText": memory_type}
            )

        gql = {
            "query": (
                '{ Get { BotMemory('
                f'limit: {limit}, '
                f'nearText: {{concepts: ["{query[:200]}"]}}, '
                f'where: {json.dumps(where_filter)}'
                ') { content memory_type context outcome timestamp } } }'
            )
        }
        try:
            data = json.dumps(gql).encode()
            req = _req.Request(
                f"{self.WEAVIATE_URL}/v1/graphql",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                memories = result.get("data", {}).get("Get", {}).get("BotMemory", [])
                return memories
        except Exception as e:
            self.log(f"Recall failed: {e}", "error")
            return []

    def recall_all(self, limit: int = 20) -> list[dict]:
        """Get this bot's most recent memories (no semantic search, just latest)."""
        import urllib.request as _req
        gql = {
            "query": (
                '{ Get { BotMemory('
                f'limit: {limit}, '
                f'where: {{path: ["bot_name"], operator: Equal, valueText: "{self.name}"}}'
                ') { content memory_type context outcome timestamp } } }'
            )
        }
        try:
            data = json.dumps(gql).encode()
            req = _req.Request(
                f"{self.WEAVIATE_URL}/v1/graphql",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with _req.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                return result.get("data", {}).get("Get", {}).get("BotMemory", [])
        except Exception as e:
            self.log(f"Recall_all failed: {e}", "error")
            return []

    # ── Self-Modification (Phase 4) ────────────────────────────────────────────

    def propose_code_change(self, new_code: str, rationale: str, change_summary: str):
        """Propose a change to this bot's own bot.py.
        Goes through ARC review. If approved, Weld applies it and restarts the container."""
        import difflib
        # Read current code
        bot_dir = BASE_DIR / "bots" / self.name
        current_file = bot_dir / "bot.py"
        try:
            current_code = current_file.read_text()
        except Exception as e:
            self.log(f"Cannot read own code for self-mod: {e}", "error")
            return False

        # Generate unified diff
        diff = list(difflib.unified_diff(
            current_code.splitlines(keepends=True),
            new_code.splitlines(keepends=True),
            fromfile=f"{self.name}/bot.py (current)",
            tofile=f"{self.name}/bot.py (proposed)",
        ))
        if not diff:
            self.log("Proposed code is identical to current — no change needed")
            return False

        diff_text = "".join(diff)
        self.push_to_bus("arc", {
            "type": "code_proposal",
            "source": self.name,
            "target_bot": self.name,
            "diff": diff_text,
            "new_code": new_code,
            "rationale": rationale,
            "change_summary": change_summary,
            "lines_changed": len([l for l in diff if l.startswith("+") or l.startswith("-")]),
            "current_version": self.instructions.get("version", 1),
        })
        self.remember(
            content=f"Proposed self-mod: {change_summary[:100]}",
            memory_type="decision",
            context=f"diff: {len(diff)} lines changed",
            outcome="sent to arc for review",
        )
        self.log(f"Code change proposal sent to ARC: {change_summary[:80]}")
        return True

    def read_own_code(self) -> str:
        """Read this bot's current bot.py source code."""
        bot_dir = BASE_DIR / "bots" / self.name
        try:
            return (bot_dir / "bot.py").read_text()
        except Exception as e:
            self.log(f"Cannot read own code: {e}", "error")
            return ""

    # ── Bus ───────────────────────────────────────────────────────────────────

    def push_to_bus(self, recipient: str, payload: dict):
        try:
            bus.push(self.name, recipient, payload)
            self.log(f"→ {recipient}: {list(payload.keys())}")
        except Exception as e:
            self.log(f"Bus push failed: {e}", "error")

    def pull_from_bus(self, limit: int = 50) -> list[dict]:
        try:
            return bus.pull(self.name, limit=limit)
        except Exception as e:
            self.log(f"Bus pull failed: {e}", "error")
            return []

    # ── Arc Rejection Handling ────────────────────────────────────────────────

    def process_rejections(self, messages: list[dict]) -> tuple[list[dict], list[dict]]:
        """Split bus messages into rejections and non-rejections.
        Logs each rejection with actionable guidance. Returns (rejections, other_messages)."""
        rejections = []
        other = []
        for msg in messages:
            payload = msg["payload"]
            if payload.get("type") == "arc_rejection":
                rejections.append(payload)
                self._handle_rejection(payload)
            else:
                other.append(msg)
        return rejections, other

    def _handle_rejection(self, rejection: dict):
        """Log rejection with actionable feedback, save to history AND Weaviate."""
        orig_type = rejection.get("original_type", "?")
        reason = rejection.get("reason", "no reason given")
        guidance = rejection.get("guidance", "")
        confidence = rejection.get("confidence", 0)
        summary = rejection.get("proposal_summary", "")

        self.log(
            f"ARC REJECTED my '{orig_type}' (conf={confidence:.2f}): {reason[:120]}"
        )
        if guidance:
            self.log(f"  GUIDANCE: {guidance[:150]}")

        # Remember rejection in Weaviate so bot can learn across restarts
        self.remember(
            content=f"Rejected {orig_type}: {reason}. {guidance}",
            memory_type="rejection",
            context=f"proposal: {summary}" if summary else orig_type,
            outcome=f"rejected conf={confidence:.2f}",
        )

        # Save to per-bot rejection history (last 20 rejections)
        history_file = STATUS_DIR / f"{self.name}_rejections.json"
        history = []
        if history_file.exists():
            try:
                history = json.loads(history_file.read_text())
            except Exception:
                pass
        history.append({
            "type": orig_type,
            "reason": reason,
            "guidance": guidance,
            "confidence": confidence,
            "summary": summary,
            "timestamp": rejection.get("rejected_at", ""),
        })
        # Keep only last 20
        history = history[-20:]
        try:
            history_file.write_text(json.dumps(history, indent=2))
        except Exception as e:
            self.log(f"Failed to save rejection history: {e}", "error")

    def get_recent_rejections(self, limit: int = 5) -> list[dict]:
        """Read recent rejection history for this bot. Useful for self-improvement."""
        history_file = STATUS_DIR / f"{self.name}_rejections.json"
        if not history_file.exists():
            return []
        try:
            history = json.loads(history_file.read_text())
            return history[-limit:]
        except Exception:
            return []

    # ── Main loop ─────────────────────────────────────────────────────────────

    @abstractmethod
    def tick(self):
        """Override in each bot — called once per interval."""

    def run(self):
        self.log(f"{self.name} starting — interval {self.interval}s, zone {self.zone}")
        self._write_status("ACTIVE", "Started")
        if self.name != "arc":
            _stagger = int(hashlib.md5(self.name.encode()).hexdigest(), 16) % self.interval
            self.log(f"{self.name} staggered start in {_stagger}s")
            time.sleep(_stagger)
        while True:
            try:
                self.tick()
                self.heartbeat()
            except Exception as e:
                self.log(f"tick() error: {e}", "error")
                self._write_status("ERROR", str(e)[:120])
            time.sleep(self.interval)
