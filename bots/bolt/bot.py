"""
bolt.py — LEFT FOOT zone. Parses raw session logs in /mega/logs/.
Extracts patterns: repeated questions, topics MEGA handles well/poorly.
Pushes pattern report to Sparky.
Interval: 10 min
"""
import os
import json
import re
from collections import Counter
from pathlib import Path
from bot_base import BaseBot
from knowledge import BoltKnowledge
import mega_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
LOGS_DIR = _MEGA / "logs"

PATTERN_SYSTEM = (
    "You are an analyst. Given a list of conversation log excerpts, identify: "
    "1) The top 3 most repeated question types or topics. "
    "2) Topics MEGA handles confidently. "
    "3) Topics where MEGA seems to struggle or give vague answers. "
    "Respond with JSON: {\"top_topics\": [\"...\"], \"strong\": [\"...\"], \"weak\": [\"...\"]}"
)


class BoltBot(BaseBot):
    def __init__(self):
        super().__init__("bolt", "leftFoot", 600)
        self.knowledge = BoltKnowledge(self)

    def tick(self):
        excerpts = self._collect_log_excerpts()
        if not excerpts:
            self.heartbeat("No log content to analyze")
            return

        self.log(f"Analyzing {len(excerpts)} log lines for patterns")

        # Quick frequency count for common keywords
        words = []
        for line in excerpts:
            words.extend(re.findall(r"\b[a-z]{4,}\b", line.lower()))
        top_words = [w for w, _ in Counter(words).most_common(20)
                     if w not in {"mega","that","this","with","have","from","what","your","are","you"}]

        # LLM pattern analysis
        sample = "\n".join(excerpts[:40])
        try:
            raw = mega_client.ask_bot(
                f"Log excerpts:\n{sample}\n\nTop frequent words: {top_words[:10]}",
                system=self.build_system_prompt(PATTERN_SYSTEM),
                temperature=0.3,
                num_predict=300,
            )
            start = raw.find("{")
            end   = raw.rfind("}") + 1
            patterns = json.loads(raw[start:end]) if start >= 0 else {}
        except Exception as e:
            self.log(f"Pattern analysis failed: {e}", "error")
            patterns = {"top_topics": top_words[:5], "strong": [], "weak": []}

        self.push_to_bus("sparky", {
            "type":       "pattern_report",
            "source":     "bolt",
            "patterns":   patterns,
            "top_words":  top_words[:10],
            "sample_count": len(excerpts),
        })
        self.log(f"Pattern report pushed to sparky — top topics: {patterns.get('top_topics', [])[:3]}")
        self.heartbeat(f"Pattern report: {len(patterns.get('top_topics',[]))} topics → sparky")

    def _collect_log_excerpts(self) -> list[str]:
        excerpts = []
        for log_file in sorted(LOGS_DIR.glob("*.log"))[:8]:
            if log_file.name.startswith("bolt"):
                continue
            try:
                lines = log_file.read_text(errors="ignore").splitlines()
                # Grab last 20 non-empty lines
                relevant = [l.strip() for l in lines if l.strip()][-20:]
                excerpts.extend(relevant)
            except Exception:
                pass
        return excerpts


if __name__ == "__main__":
    BoltBot().run()
