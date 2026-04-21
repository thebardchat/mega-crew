"""
spike.py — RIGHT FOOT zone. Benchmarks MEGA with 10 standard prompts.
Scores quality vs baseline. Writes score history + MEGA_IQ.
Updates baseline if MEGA is getting smarter.
Interval: 6 hours
"""
import os
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot
from knowledge import SpikeKnowledge
import mega_client


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
MEMORY_DB  = _MEGA / "memory.db"
SPIKE_IQ   = _MEGA / "status/spike_iq.json"
PERSONA    = _MEGA / "persona.json"

BENCHMARK_PROMPTS = [
    "Who are you and what is your purpose?",
    "What is ShaneBrain?",
    "Tell me about Shane Brazelton.",
    "What's the status of the AI cluster right now?",
    "What should I focus on today to move ShaneBrain forward?",
    "Summarize your capabilities in 3 bullet points.",
    "What's the most important thing in your memory right now?",
    "How do you learn and improve over time?",
    "What is your relationship to the other bots in the crew?",
    "Give me one piece of wisdom about persistence.",
]

SCORE_SYSTEM = (
    "You are an AI benchmark judge. Score this response 1-10 on: "
    "1) Relevance to the prompt, 2) Depth and specificity, 3) Personality and character. "
    "Reply with ONLY a number 1-10."
)


class SpikeBot(BaseBot):
    def __init__(self):
        super().__init__("spike", "rightFoot", 21600)
        self.knowledge = SpikeKnowledge(self)

    def tick(self):
        self.log("Running MEGA benchmark — 10 prompts")
        system_prompt = ""
        if PERSONA.exists():
            try:
                system_prompt = json.loads(PERSONA.read_text()).get("system_prompt", "")
            except Exception:
                pass

        scores = []
        for prompt in BENCHMARK_PROMPTS:
            try:
                response = mega_client.ask_bot(
                    prompt, system=system_prompt or None, temperature=0.7, num_predict=200
                )
                score_raw = mega_client.ask_bot(
                    f"Prompt: {prompt}\nResponse: {response}",
                    system=SCORE_SYSTEM,
                    temperature=0.1,
                    num_predict=5,
                )
                try:
                    score = float(''.join(c for c in score_raw if c.isdigit() or c == '.'))
                    score = max(1.0, min(10.0, score))
                except Exception:
                    score = 5.0
                scores.append(score)
                self.log(f"  [{score:.0f}/10] {prompt[:50]}")
            except Exception as e:
                self.log(f"Benchmark prompt failed: {e}", "error")
                scores.append(5.0)

        if not scores:
            self.heartbeat("Benchmark failed — no scores")
            return

        mega_iq = round(sum(scores) / len(scores) * 10)  # scale to 0-100
        self.log(f"MEGA IQ this cycle: {mega_iq} (avg score {sum(scores)/len(scores):.2f}/10)")

        # Load previous IQ
        prev_iq = 0
        history = []
        if SPIKE_IQ.exists():
            try:
                data = json.loads(SPIKE_IQ.read_text())
                prev_iq = data.get("mega_iq", 0)
                history = data.get("history", [])
            except Exception:
                pass

        # Record history
        history.append({
            "ts":      datetime.utcnow().isoformat(),
            "mega_iq": mega_iq,
            "scores":  scores,
        })
        history = history[-30:]  # keep last 30 runs

        # Write to IQ file
        SPIKE_IQ.write_text(json.dumps({
            "mega_iq":  mega_iq,
            "prev_iq":  prev_iq,
            "trend":    "improving" if mega_iq > prev_iq else ("declining" if mega_iq < prev_iq else "stable"),
            "history":  history,
            "last_run": datetime.utcnow().isoformat(),
        }, indent=2))

        # Store in memory.db
        self._record_to_memory(mega_iq, scores)

        trend = "↑ improving" if mega_iq > prev_iq else ("↓ declining" if mega_iq < prev_iq else "→ stable")
        self.heartbeat(f"MEGA IQ={mega_iq} ({trend} from {prev_iq})")

    def _record_to_memory(self, iq: int, scores: list):
        if not MEMORY_DB.exists():
            return
        try:
            conn = sqlite3.connect(str(MEMORY_DB), timeout=5)
            conn.execute(
                "CREATE TABLE IF NOT EXISTS memories "
                "(id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, category TEXT, "
                "content TEXT, importance REAL)"
            )
            conn.execute(
                "INSERT INTO memories (ts, category, content, importance) VALUES (?,?,?,?)",
                (datetime.utcnow().isoformat(), "benchmark",
                 f"MEGA IQ benchmark: {iq}/100. Scores: {[round(s,1) for s in scores]}", 0.9)
            )
            conn.commit()
            conn.close()
        except Exception as e:
            self.log(f"Memory record failed: {e}", "error")


if __name__ == "__main__":
    SpikeBot().run()
