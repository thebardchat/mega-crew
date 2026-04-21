"""
stomp.py — LEFT FOOT zone. Scans memory.db for duplicate or contradictory memories.
Flags conflicts. Pushes resolution candidates to Arc.
Interval: 1 hour
"""
import os
import sqlite3
import json
from pathlib import Path
from bot_base import BaseBot
from knowledge import StompKnowledge
import mega_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
MEMORY_DB = _MEGA / "memory.db"

CONFLICT_SYSTEM = (
    "You are a memory conflict resolver. Given two memory entries from an AI system, "
    "determine if they are: 1) Duplicates (same info), 2) Contradictory (opposite facts), "
    "or 3) Complementary (different but compatible). "
    "Respond with JSON: {\"type\": \"duplicate|contradiction|complementary\", "
    "\"resolution\": \"keep_first|keep_second|merge|keep_both\", \"merged_content\": \"...\"}"
)


class StompBot(BaseBot):
    def __init__(self):
        super().__init__("stomp", "leftFoot", 3600)
        self.knowledge = StompKnowledge(self)

    def tick(self):
        # Process any arc rejections first
        bus_msgs = self.pull_from_bus()
        if bus_msgs:
            rejections, _ = self.process_rejections(bus_msgs)
            if rejections:
                self.log(f"Received {len(rejections)} arc rejections — learning from feedback")

        memories = self._fetch_memories(200)
        if len(memories) < 2:
            self.heartbeat("Not enough memories to compare")
            return

        self.log(f"Scanning {len(memories)} memories for conflicts")
        conflicts_found = 0
        resolutions = []

        # Compare recent memories pairwise (capped to avoid too many LLM calls)
        recent = memories[-30:]
        checked = set()

        for i, m1 in enumerate(recent):
            for m2 in recent[i+1:i+4]:  # compare each to next 3
                pair_key = f"{m1['id']}-{m2['id']}"
                if pair_key in checked:
                    continue
                checked.add(pair_key)

                # Quick similarity check (avoid LLM for obviously unrelated)
                words1 = set(m1["content"].lower().split())
                words2 = set(m2["content"].lower().split())
                overlap = len(words1 & words2) / max(len(words1 | words2), 1)
                if overlap < 0.25:
                    continue

                try:
                    raw = mega_client.ask_bot(
                        f"Memory 1 (id={m1['id']}): {m1['content']}\n\n"
                        f"Memory 2 (id={m2['id']}): {m2['content']}",
                        system=self.build_system_prompt(CONFLICT_SYSTEM),
                        temperature=0.2,
                        num_predict=150,
                    )
                    start = raw.find("{")
                    end   = raw.rfind("}") + 1
                    result = json.loads(raw[start:end]) if start >= 0 else {}
                    conflict_type = result.get("type", "complementary")

                    if conflict_type in ("duplicate", "contradiction"):
                        conflicts_found += 1
                        resolutions.append({
                            "m1_id":      m1["id"],
                            "m2_id":      m2["id"],
                            "type":       conflict_type,
                            "resolution": result.get("resolution", "keep_both"),
                            "merged":     result.get("merged_content", ""),
                        })
                except Exception as e:
                    self.log(f"Conflict check error: {e}", "error")

        if resolutions:
            self.push_to_bus("arc", {
                "type":       "memory_conflicts",
                "source":     "stomp",
                "conflicts":  resolutions,
                "total_scanned": len(recent),
            })
            self.log(f"Found {conflicts_found} conflicts → pushed to arc")
            self.heartbeat(f"Found {conflicts_found} memory conflicts → arc")
        else:
            self.heartbeat(f"Scanned {len(recent)} recent memories — no conflicts")

    def _fetch_memories(self, limit: int) -> list[dict]:
        if not MEMORY_DB.exists():
            return []
        try:
            conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
            rows = conn.execute(
                "SELECT id, content FROM memories ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
            conn.close()
            return [{"id": r[0], "content": r[1]} for r in rows]
        except Exception as e:
            self.log(f"Memory fetch error: {e}", "error")
            return []


if __name__ == "__main__":
    StompBot().run()
