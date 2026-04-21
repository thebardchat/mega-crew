"""
forge.py — RIGHT FOOT zone. Daily at 3am (or on first run after 3am).
Reads Bolt's pattern reports from last 24hrs. If a pattern appears 3+ times
(something MEGA is always asked that has no FastMCP tool), drafts a new tool
stub in /mnt/shanebrain-raid/shanebrain-core/tools/pending/ for Shane to review.
Interval: 24 hours
"""
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from collections import Counter
from bot_base import BaseBot
from knowledge import ForgeKnowledge
import mega_client
import bus as bus_module

_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
PENDING_DIR = _MEGA / "tools" / "pending"
PENDING_DIR.mkdir(parents=True, exist_ok=True)

TOOL_STUB_SYSTEM = (
    "You are a tool architect for an AI system called ShaneBrain. "
    "Based on the pattern (a recurring question or need that has no automated tool), "
    "write a Python FastAPI endpoint stub that would fulfill this need. "
    "Include: route decorator, function signature, docstring, and a TODO body. "
    "Keep it under 40 lines. Format as valid Python."
)


class ForgeBot(BaseBot):
    def __init__(self):
        super().__init__("forge", "rightFoot", 86400)
        self.knowledge = ForgeKnowledge(self)
        self._last_run_date = None

    def tick(self):
        # Only run at/after 3am
        now = datetime.now()
        if now.hour < 3:
            self.log(f"Before 3am ({now.hour}h) — Forge waits until 3am")
            self.heartbeat(f"Waiting for 3am (now {now.hour}:{now.minute:02d})")
            return

        today = now.date().isoformat()
        if self._last_run_date == today:
            self.log("Already ran today — skipping")
            self.heartbeat(f"Already ran {today}")
            return

        self._last_run_date = today
        self.log(f"Forge running — scanning patterns from last 24hrs")

        # Pull all messages from bolt (pattern reports) — also read bus directly for history
        messages = self.pull_from_bus()
        bolt_patterns = []

        for msg in messages:
            payload = msg.get("payload", {})
            if payload.get("type") == "pattern_report":
                bolt_patterns.append(payload)

        if not bolt_patterns:
            self.log("No pattern reports from Bolt in last 24hrs")
            self.heartbeat("No Bolt patterns to act on today")
            return

        # Aggregate all weak topics
        all_weak = []
        all_topics = []
        for p in bolt_patterns:
            patterns = p.get("patterns", {})
            all_weak.extend(patterns.get("weak", []))
            all_topics.extend(patterns.get("top_topics", []))

        freq = Counter(all_weak + all_topics)
        repeated = [(topic, count) for topic, count in freq.items() if count >= 3]

        if not repeated:
            self.log(f"No topics with 3+ occurrences (max was {freq.most_common(1)})")
            self.heartbeat("No patterns met 3-hit threshold today")
            return

        self.log(f"Found {len(repeated)} patterns with 3+ hits: {repeated[:5]}")
        stubs_written = 0

        for topic, count in repeated[:3]:  # max 3 stubs per day
            safe_name = topic.lower().replace(" ", "_")[:40]
            stub_file = PENDING_DIR / f"tool_{safe_name}_{today}.py"
            if stub_file.exists():
                continue

            try:
                stub = mega_client.ask_bot(
                    f"Pattern: '{topic}' has been asked {count} times with no dedicated tool. "
                    "Draft a FastAPI endpoint stub for this.",
                    system=TOOL_STUB_SYSTEM,
                    temperature=0.5,
                    num_predict=400,
                )
                content = (
                    f"# Forge-generated tool stub — {datetime.utcnow().isoformat()}\n"
                    f"# Pattern: '{topic}' (seen {count}x in 24hrs)\n"
                    f"# Status: PENDING SHANE REVIEW\n\n"
                    f"{stub}\n"
                )
                stub_file.write_text(content)
                stubs_written += 1
                self.log(f"Tool stub drafted: {stub_file.name}")
            except Exception as e:
                self.log(f"Stub generation failed for '{topic}': {e}", "error")

        self.heartbeat(f"Drafted {stubs_written} tool stubs in tools/pending/ for Shane")


if __name__ == "__main__":
    ForgeBot().run()
