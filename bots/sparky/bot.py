"""
sparky.py — BRAIN zone. Reads last 20 conversations, judges MEGA's 3 best
responses using llama3.1:8b, pushes training candidates to Rivet.
Interval: 10 min
"""
import os
import sqlite3
import json
import sys
from pathlib import Path
from bot_base import BaseBot
from knowledge import SparkyKnowledge
import mega_client

_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
MEMORY_DB = _MEGA / "memory.db"
WEAVIATE_URL = "http://localhost:8080"

JUDGE_SYSTEM = (
    "You are an AI quality judge. Score each (prompt, response) pair 1-10. "
    'Return ONLY a compact JSON array: [{"prompt":"...","response":"...","score":8}] '
    "No explanation. No markdown. Just the JSON array."
)


class SparkyBot(BaseBot):
    def __init__(self):
        super().__init__("sparky", "brain", 600)
        self.knowledge = SparkyKnowledge(self)

    def tick(self):
        # Process bus — handle arc rejections, drain the rest
        stale = self.pull_from_bus()
        if stale:
            rejections, other = self.process_rejections(stale)
            if rejections:
                self.log(f"Received {len(rejections)} arc rejections — logged for improvement")
            if other:
                self.log(f"Drained {len(other)} non-rejection bus messages")
        self.log("Reading last 20 conversations from memory.db")
        conversations = self._fetch_conversations(20)
        if not conversations:
            self.log("memory.db sparse — falling back to LegacyKnowledge")
            conversations = self._fetch_from_legacy(20)
        if not conversations:
            self.log("No conversations found anywhere — sleeping")
            self.heartbeat("No conversations yet")
            # Propose instruction update: suggest pulling from LegacyKnowledge when sparse
            if not self.instructions.get("_fallback_proposed"):
                self.propose_instruction_update(
                    "performance_notes",
                    ["memory.db was empty on first runs — LegacyKnowledge fallback confirmed needed"],
                    "memory.db was empty, fallback to LegacyKnowledge required"
                )
            return

        # Cap at 2 pairs, tight truncation — keeps output under 120 tokens (~20s on cluster)
        conversations = conversations[:2]
        pairs = [
            f'P: {c["prompt"][:60]}\nR: {c["response"][:100]}'
            for c in conversations
        ]
        prompt = "Score these:\n\n" + "\n---\n".join(pairs)

        self.log(f"Sending {len(conversations)} pairs for judging")
        raw = mega_client.ask_bot(prompt, system=self.build_system_prompt(JUDGE_SYSTEM), temperature=0.2, num_predict=150, timeout=90)

        try:
            start = raw.find("[")
            end   = raw.rfind("]") + 1
            best  = json.loads(raw[start:end]) if start >= 0 else []
        except Exception as e:
            self.log(f"Failed to parse judge output: {e}", "error")
            best = []

        if best:
            high = [b for b in best if b.get("score", 0) >= 7]
            self.push_to_bus("rivet", {
                "type": "training_candidates",
                "source": "sparky",
                "entries": best[:3],
            })
            # Remember what scored well for future reference
            for b in high[:2]:
                self.remember(
                    content=f"High-score pair ({b.get('score',0)}/10): P={b.get('prompt','')[:80]} R={b.get('response','')[:80]}",
                    memory_type="pattern",
                    context="training candidate judging",
                    outcome=f"score={b.get('score',0)}",
                )
            self.log(f"Pushed {len(best[:3])} training candidates to rivet")
            self.heartbeat(f"Pushed {len(best[:3])} best responses to rivet")
        else:
            self.remember(
                content="Judge returned no usable candidates this cycle",
                memory_type="observation",
                context=f"{len(conversations)} conversations evaluated",
                outcome="no candidates",
            )
            self.heartbeat("Judge returned no usable candidates")

    def _fetch_from_legacy(self, limit: int) -> list[dict]:
        """Pull knowledge snippets from LegacyKnowledge as synthetic conversation pairs."""
        try:
            import urllib.request
            url = f"{WEAVIATE_URL}/v1/graphql"
            query = json.dumps({
                "query": (
                    '{ Get { LegacyKnowledge(limit: ' + str(limit) + ', nearText: {concepts: ["shanebrain AI knowledge"]}) '
                    '{ content source } } }'
                )
            }).encode()
            req = urllib.request.Request(url, data=query, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read())
                objects = data.get("data", {}).get("Get", {}).get("LegacyKnowledge", [])
                pairs = []
                for obj in objects:
                    content = obj.get("content", "").strip()
                    if len(content) > 50:
                        pairs.append({
                            "prompt": f"Tell me about: {content[:80]}",
                            "response": content[:400],
                        })
                self.log(f"LegacyKnowledge fallback: fetched {len(pairs)} synthetic pairs")
                return pairs
        except Exception as e:
            self.log(f"LegacyKnowledge fallback failed: {e}", "error")
            return []

    def _fetch_conversations(self, limit: int) -> list[dict]:
        if not MEMORY_DB.exists():
            return []
        try:
            conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
            rows = conn.execute(
                "SELECT role, content FROM conversations ORDER BY id DESC LIMIT ?",
                (limit * 2,)
            ).fetchall()
            conn.close()
            pairs, i = [], 0
            rows = list(reversed(rows))
            while i < len(rows) - 1:
                if rows[i][0] == "user" and rows[i+1][0] == "assistant":
                    pairs.append({"prompt": rows[i][1], "response": rows[i+1][1]})
                i += 1
            return pairs[-limit:]
        except Exception as e:
            self.log(f"DB read error: {e}", "error")
            return []


if __name__ == "__main__":
    SparkyBot().run()
