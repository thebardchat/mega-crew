"""
rivet.py — LEFT HAND zone. Consumes Sparky + Glitch queues.
Deduplicates, formats as clean JSONL training pairs, pushes batch to Arc.
Interval: 2 min (fast consumer)
"""
import os
import hashlib
import json
from pathlib import Path
from bot_base import BaseBot
from knowledge import RivetKnowledge


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
SEEN_FILE = _MEGA / "status/rivet_seen.json"


class RivetBot(BaseBot):
    def __init__(self):
        super().__init__("rivet", "leftHand", 120)
        self.knowledge = RivetKnowledge(self)
        self._seen: set = self._load_seen()

    def _load_seen(self) -> set:
        if SEEN_FILE.exists():
            try:
                return set(json.loads(SEEN_FILE.read_text()))
            except Exception:
                pass
        return set()

    def _save_seen(self):
        # Keep only last 2000 hashes
        seen_list = list(self._seen)[-2000:]
        self._seen = set(seen_list)
        SEEN_FILE.write_text(json.dumps(seen_list))

    def _hash(self, text: str) -> str:
        return hashlib.md5(text.encode()).hexdigest()

    def tick(self):
        messages = self.pull_from_bus()
        if not messages:
            self.heartbeat("Queue empty — waiting")
            return

        # Process any arc rejections first — learn from feedback
        rejections, messages = self.process_rejections(messages)
        if rejections:
            self.log(f"Received {len(rejections)} arc rejections — adjusting quality filters")

        raw_pairs = []
        for msg in messages:
            payload = msg["payload"]
            msg_type = payload.get("type", "")

            if msg_type == "training_candidates":
                for entry in payload.get("entries", []):
                    p = entry.get("prompt", "")
                    r = entry.get("response", "")
                    s = entry.get("score", 0)
                    if p and r:
                        raw_pairs.append({"prompt": p, "response": r, "score": s,
                                          "source": payload.get("source", "sparky")})

            elif msg_type == "adversarial_failures":
                for entry in payload.get("entries", []):
                    p = entry.get("prompt", "")
                    r = entry.get("response", "")
                    if p and r:
                        raw_pairs.append({
                            "prompt": f"[ADVERSARIAL] {p}",
                            "response": r,
                            "score": -1,  # flag for Arc review
                            "source": "glitch",
                            "issue": entry.get("issue", ""),
                            "severity": entry.get("severity", "low"),
                        })

        # Deduplicate
        clean = []
        for pair in raw_pairs:
            h = self._hash(pair["prompt"] + pair["response"])
            if h not in self._seen:
                self._seen.add(h)
                clean.append(pair)

        self._save_seen()

        if not clean:
            self.heartbeat(f"Processed {len(messages)} messages — all duplicates")
            return

        self.push_to_bus("arc", {
            "type":    "training_batch",
            "source":  "rivet",
            "entries": clean,
            "count":   len(clean),
        })
        self.log(f"Pushed {len(clean)} deduplicated pairs to Arc")
        self.heartbeat(f"Batched {len(clean)} pairs → arc")


if __name__ == "__main__":
    RivetBot().run()
