"""
neon.py — BRAIN zone. Watches training.jsonl for new entries.
Generates nomic-embed-text embeddings, writes to Weaviate MEGABrain.
Interval: 5 min
"""
import os
import json
from pathlib import Path
from bot_base import BaseBot
from knowledge import NeonKnowledge
import weaviate_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
TRAINING_FILE  = _MEGA / "training.jsonl"
WATERMARK_FILE = _MEGA / "status/neon_watermark.txt"


class NeonBot(BaseBot):
    def __init__(self):
        super().__init__("neon", "brain", 300)
        self.knowledge = NeonKnowledge(self)
        self._watermark = self._load_watermark()

    def _load_watermark(self) -> int:
        if WATERMARK_FILE.exists():
            try:
                return int(WATERMARK_FILE.read_text().strip())
            except Exception:
                pass
        return 0

    def _save_watermark(self, pos: int):
        WATERMARK_FILE.write_text(str(pos))
        self._watermark = pos

    def tick(self):
        if not TRAINING_FILE.exists():
            self.heartbeat("No training.jsonl yet")
            return

        new_entries = []
        current_pos = 0

        with open(TRAINING_FILE, "r") as f:
            f.seek(self._watermark)
            for line in f:
                line = line.strip()
                if line:
                    try:
                        new_entries.append(json.loads(line))
                    except Exception:
                        pass
            current_pos = f.tell()

        if not new_entries:
            self.heartbeat(f"No new entries (watermark={self._watermark})")
            return

        self.log(f"Found {len(new_entries)} new training entries — embedding")
        embedded = 0

        for entry in new_entries:
            try:
                prompt   = entry.get("prompt", "")
                response = entry.get("response", entry.get("completion", ""))
                content  = f"Q: {prompt}\nA: {response}".strip()
                if not content:
                    continue
                weaviate_client.add_object(
                    content=content,
                    source="training.jsonl",
                    category="training",
                    bot="neon",
                    score=entry.get("score", 0.0),
                )
                embedded += 1
            except Exception as e:
                self.log(f"Embed failed for entry: {e}", "error")

        self._save_watermark(current_pos)
        self.log(f"Embedded {embedded}/{len(new_entries)} entries into MEGABrain")
        self.heartbeat(f"Embedded {embedded} new entries into Weaviate")


if __name__ == "__main__":
    NeonBot().run()
