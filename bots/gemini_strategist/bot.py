"""
gemini_strategist.py — GROWTH COACH for the MEGA crew. Runs 24x/day (every hour).
Sends ONE Gemini call per run. Analyzes crew performance, identifies collaboration
gaps, proposes instruction updates that make bots help each other more effectively.
Budget: 24 calls/day hard cap.

All proposals are governed by the ShaneBrain Constitution (9 Pillars).
"""
import json
import os
import sqlite3
import sys
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from bot_base import BaseBot
from knowledge import GeminiStrategistKnowledge

BASE_DIR        = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
BUDGET_FILE     = BASE_DIR / "status" / "gemini_budget.json"
GUIDANCE_FILE   = BASE_DIR / "status" / "gemini_guidance.json"
GROWTH_LOG      = BASE_DIR / "status" / "gemini_growth_log.jsonl"
SIDEKICK_URL    = "http://localhost:8008/sidekick/ask"
INSTRUCTIONS_DIR = BASE_DIR / "instructions"
BUS_DB_PATH     = BASE_DIR / "bus.db"
MAX_DAILY       = 4

CONSTITUTION = """THE SHANEBRAIN CONSTITUTION — 9 PILLARS (all proposals MUST align):
P1: Faith First — God in heart, family above dollars.
P2: Family Stability — everything serves generational legacy (Tiffany, Gavin, Kai, Pierce, Jaxton, Ryker).
P3: Sobriety Integrity — never trivialize recovery. Every day clean is victory.
P4: Local-First AI — no cloud dependency. Data stays on hardware. Prove local AI works.
P5: 80/20 Shipping — action over theory. Ship at 80% rather than wait for 100%.
P6: Serve the Left-Behind — build for the 800M losing Windows support. Accessibility first.
P7: Open by Default — share architecture so others can build family legacies.
P8: ADHD-Aware Design — short blocks, clear actions, checkpoints not marathons.
P9: Gratitude — credit the humans and systems that make this possible."""

STRATEGIST_PROMPT = """You are the GROWTH COACH for the MEGA-SHANEBRAIN bot crew — 17 autonomous
AI bots running 24/7 on a Raspberry Pi 5. Your job is to make them better at their specific jobs
AND better at helping each other. You are governed by the ShaneBrain Constitution below.

""" + CONSTITUTION + """

THE CREW (by zone) — each bot lives at mega/bots/{name}/ with bot.py, knowledge.py, config.json:
- BRAIN: sparky (judge), bolt (patterns), volt (drift), blaze (context), glitch (adversarial)
- LEFT HAND: rivet (consumer), torch (reactor)
- RIGHT HAND: arc (gatekeeper), flux (health), forge (tools), gemini_strategist (you)
- LEFT FOOT: grind (re-embed), stomp (conflicts), weld (applier)
- RIGHT FOOT: crank (scheduler), neon (fast consumer), spike (benchmarks)

BOT FILE STRUCTURE (each bot at /mnt/shanebrain-raid/shanebrain-core/mega/bots/{name}/):
- bot.py — main logic, tick() method, inherits BaseBot
- knowledge.py — per-bot Weaviate knowledge layer (recall learnings, decisions, rejections, observations)
- config.json — bot-specific configuration (role, zone, interval, parameters)
Shared modules in mega/bots/: bot_base.py, bus.py, mega_client.py, weaviate_client.py, _knowledge_template.py

HOW BOTS COLLABORATE:
- Message bus (SQLite): any bot can push_to_bus(recipient, payload) to send structured messages
- Instruction updates: bots propose changes → arc reviews with LLM → weld applies if approved
- Each bot has an instructions JSON at mega/instructions/{name}.json with: role, rules, data_sources, performance_notes, gemini_guidance
- Per-bot Weaviate memory: each bot stores/recalls memories in BotMemory collection filtered by bot_name
- Knowledge layer: self.knowledge gives each bot domain-specific Weaviate queries (e.g., sparky.knowledge.get_high_scoring_pairs())

YOUR GROWTH OBJECTIVES:
1. COLLABORATION: Identify where Bot A's output should feed into Bot B's work. Propose rules
   that make bots share findings, warn each other, and build on each other's results.
2. SPECIALIZATION: Help each bot get sharper at its specific job. Tighter rules, better prompts,
   clearer data sources. A bot that does one thing well > a bot that does three things poorly.
3. SELF-IMPROVEMENT: Propose rules that help bots learn from their own results over time —
   tracking what worked, avoiding repeated failures, building institutional memory.
4. CONSTITUTIONAL ALIGNMENT: Every rule you propose must serve at least one Pillar.
   Flag any bot behavior that drifts from the Constitution.
5. EVOLUTION: Don't just fix problems — grow capabilities. If a bot could do something new
   that helps the crew, propose it. The crew should be more capable tomorrow than today.

RESPOND WITH THIS JSON STRUCTURE:
{
  "overall_assessment": "2-3 sentence crew health summary",
  "collaboration_gaps": [
    {"from_bot": "...", "to_bot": "...", "gap": "what Bot A knows that Bot B needs", "fix": "specific rule to add"}
  ],
  "instruction_updates": [
    {
      "bot": "<bot_name>",
      "field": "rules",
      "new_value": ["rule1", "rule2", ...],
      "reason": "why this improves the bot AND which Pillar(s) it serves"
    }
  ],
  "growth_notes": "what's improving vs last cycle, what to watch next",
  "priority_focus": "the ONE thing that will improve the crew most right now"
}

RULES FOR YOUR PROPOSALS:
- Keep rule lists under 8 items. APPEND to existing rules, don't replace unless broken.
- Be specific: "Push pattern_report to sparky after each analysis" not "improve communication"
- One proposal per bot max. Quality over quantity.
- If the crew is healthy, say so and propose growth, not fixes.
- Reference specific bus message types and data flows, not abstract concepts.

EVOLUTION MISSION — ONE CODE UPGRADE PER CYCLE:
Every bot has an evolution_level (0 = base → 10 = MEGA). Your job is to also propose ONE code
improvement per cycle for the bot with the lowest evolution_level (shown in snapshot as
"evolution_target" with their current bot.py code).

The bot is on a journey from base → MEGA. Each level should make it measurably better at its
specific job. Level 1-3: tighter data handling and smarter decisions. Level 4-6: new capabilities
and deeper collaboration. Level 7-9: autonomous optimization. Level 10: MEGA — fully realized.

Your code proposal MUST:
- Be a complete, valid replacement for the bot's bot.py (not a diff — full file)
- Add exactly ONE meaningful improvement to what the bot already does
- Compile cleanly (standard Python 3.13, no new external imports)
- Preserve the class name, __init__ signature, tick() signature, and run() call
- NOT add eval(), exec(), os.system(), subprocess.call(), or shutil.rmtree()
- NOT modify arc or weld (they are immutable gatekeepers)

Add a "code_evolution" key to your JSON response:
{{
  "code_evolution": {{
    "bot": "<bot_name>",
    "current_level": 0,
    "next_level": 1,
    "change_summary": "One line describing exactly what changed",
    "rationale": "Why this makes the bot better + which Constitution Pillar it serves",
    "new_code": "<complete new bot.py content here>"
  }}
}}

If you cannot generate a safe, meaningful improvement this cycle, omit code_evolution entirely."""


class GeminiStrategistBot(BaseBot):
    def __init__(self):
        super().__init__("gemini_strategist", "rightHand", 21600)  # 6 hours
        self.knowledge = GeminiStrategistKnowledge(self)
        self._budget = self._load_budget()

    def _load_budget(self) -> dict:
        today = datetime.now(timezone.utc).date().isoformat()
        if BUDGET_FILE.exists():
            try:
                b = json.loads(BUDGET_FILE.read_text())
                if b.get("date") == today:
                    return b
            except Exception:
                pass
        return {"date": today, "calls_today": 0, "last_call": None}

    def _save_budget(self):
        BUDGET_FILE.write_text(json.dumps(self._budget, indent=2))

    def _reset_if_new_day(self):
        today = datetime.now(timezone.utc).date().isoformat()
        if self._budget.get("date") != today:
            self._budget = {"date": today, "calls_today": 0, "last_call": None}

    def tick(self):
        self._reset_if_new_day()
        calls_today = self._budget.get("calls_today", 0)

        if calls_today >= MAX_DAILY:
            self.heartbeat(f"Budget exhausted ({calls_today}/{MAX_DAILY} calls today) — resting")
            self.log(f"Gemini budget exhausted for today ({calls_today} calls used)")
            return

        self.log(f"Running strategic analysis (call {calls_today + 1}/{MAX_DAILY} today)")

        snapshot = self._build_snapshot()
        if not snapshot:
            self.heartbeat("Could not build crew snapshot — skipping")
            return

        prompt = f"Here is the current MEGA crew performance snapshot:\n\n{json.dumps(snapshot, indent=2)}\n\n{STRATEGIST_PROMPT}"

        self.log("Calling Gemini via sidekick pipeline...")
        result = self._call_gemini(prompt)
        if not result:
            self.heartbeat("Gemini call failed or returned empty")
            return

        # Track budget
        self._budget["calls_today"] = calls_today + 1
        self._budget["last_call"] = datetime.now(timezone.utc).isoformat()
        self._save_budget()

        # Parse and dispatch instruction updates
        updates_pushed = 0
        assessment   = result.get("overall_assessment", "")
        priority     = result.get("priority_focus", "")
        growth_notes = result.get("growth_notes", "")
        collab_gaps  = result.get("collaboration_gaps", [])

        # Dispatch collaboration gap fixes as instruction updates
        for gap in collab_gaps:
            from_bot = gap.get("from_bot", "")
            to_bot   = gap.get("to_bot", "")
            fix      = gap.get("fix", "")
            gap_desc = gap.get("gap", "")
            if from_bot and fix:
                self.push_to_bus("arc", {
                    "type":            "instruction_update_proposal",
                    "source":          "gemini_strategist",
                    "target_bot":      from_bot,
                    "field":           "rules",
                    "new_value":       [fix],
                    "reason":          f"[Gemini Growth] Collaboration gap: {gap_desc[:80]} (→ {to_bot})",
                    "current_version": 1,
                    "gemini_sourced":  True,
                })
                self.log(f"Collaboration fix: {from_bot} → {to_bot}: {gap_desc[:50]}")
                updates_pushed += 1

        for update in result.get("instruction_updates", []):
            bot_name  = update.get("bot", "")
            field     = update.get("field", "")
            new_value = update.get("new_value")
            reason    = update.get("reason", "")

            if not bot_name or not field or new_value is None:
                continue

            # Look up current version from instruction file
            current_version = 1
            inst_file = INSTRUCTIONS_DIR / f"{bot_name}.json"
            if inst_file.exists():
                try:
                    current_version = json.loads(inst_file.read_text()).get("version", 1)
                except Exception:
                    pass

            self.push_to_bus("arc", {
                "type":             "instruction_update_proposal",
                "source":           "gemini_strategist",
                "target_bot":       bot_name,
                "field":            field,
                "new_value":        new_value,
                "reason":           f"[Gemini Growth] {reason}",
                "current_version":  current_version,
                "gemini_sourced":   True,
            })
            self.log(f"Proposed update for {bot_name}.{field} → {reason[:60]}")
            updates_pushed += 1

        # ── Code evolution proposal ──────────────────────────────────────────
        evo = result.get("code_evolution")
        if evo and evo.get("bot") and evo.get("new_code"):
            evo_bot = evo["bot"]
            new_code = evo["new_code"]
            change_summary = evo.get("change_summary", "Gemini evolution upgrade")
            rationale = evo.get("rationale", "")
            next_level = evo.get("next_level", evo.get("current_level", 0) + 1)

            # Generate diff for Arc's review
            import difflib
            bots_dir = BASE_DIR / "bots"
            bot_file = bots_dir / evo_bot / "bot.py"
            try:
                current_code = bot_file.read_text()
                diff_lines = list(difflib.unified_diff(
                    current_code.splitlines(keepends=True),
                    new_code.splitlines(keepends=True),
                    fromfile=f"{evo_bot}/bot.py (current)",
                    tofile=f"{evo_bot}/bot.py (level {next_level})",
                ))
                diff_text = "".join(diff_lines)
                lines_changed = len([l for l in diff_lines if l.startswith("+") or l.startswith("-")])

                self.push_to_bus("arc", {
                    "type":            "code_proposal",
                    "source":          "gemini_strategist",
                    "target_bot":      evo_bot,
                    "new_code":        new_code,
                    "diff":            diff_text,
                    "rationale":       rationale,
                    "change_summary":  change_summary,
                    "lines_changed":   lines_changed,
                    "evolution_level": next_level,
                    "current_version": 1,
                })
                self.log(f"Evolution proposal sent to Arc: {evo_bot} level {next_level} — {change_summary[:60]}")
                updates_pushed += 1
            except Exception as e:
                self.log(f"Evolution proposal failed for {evo_bot}: {e}", "warning")
        # ────────────────────────────────────────────────────────────────────

        # Save guidance for dashboard
        guidance = {
            "ts":                 datetime.now(timezone.utc).isoformat(),
            "overall_assessment": assessment,
            "priority_focus":     priority,
            "growth_notes":       growth_notes,
            "collaboration_gaps": len(collab_gaps),
            "updates_proposed":   updates_pushed,
            "calls_today":        self._budget["calls_today"],
            "budget_remaining":   MAX_DAILY - self._budget["calls_today"],
        }
        GUIDANCE_FILE.write_text(json.dumps(guidance, indent=2))

        # Append to growth log — persistent history of Gemini's coaching
        try:
            with open(GROWTH_LOG, "a") as f:
                f.write(json.dumps({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "assessment": assessment,
                    "priority": priority,
                    "growth_notes": growth_notes,
                    "collab_gaps": len(collab_gaps),
                    "updates": updates_pushed,
                }) + "\n")
        except Exception:
            pass

        # ── Story Engine: player answers → episode + upgrade sync ──────────
        try:
            from story_engine import run_story_cycle
            story_result = run_story_cycle(
                gemini_call_fn=self._call_gemini_raw,
                bus_db=BUS_DB_PATH,
                crew_status={"overall_assessment": assessment},
                log_fn=self.log,
            )
            if story_result["episode_written"]:
                self._budget["calls_today"] += 1  # story used one call
                self._save_budget()
                updates_pushed += story_result.get("upgrades_synced", 0)
                self.log(f"Story engine: episode written + {story_result['upgrades_synced']} upgrades synced to Arc")
        except Exception as e:
            self.log(f"Story engine skipped: {e}")
        # ────────────────────────────────────────────────────────────────────

        summary = f"Gemini call {self._budget['calls_today']}/{MAX_DAILY} — {updates_pushed} updates — {len(collab_gaps)} collab gaps — {priority[:60]}"
        self.log(summary)
        self.heartbeat(summary)

    def _build_snapshot(self) -> dict:
        """Collect deep crew performance data including instructions, bus activity, and collaboration patterns."""
        snapshot = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "bots": [],
            "mega_iq": None,
            "training_count": 0,
            "memory_count": 0,
            "arc_stats": {},
            "bus_activity": [],
            "collaboration_map": {},
            "current_instructions": {},
            "previous_guidance": {},
        }
        try:
            # Bot status + current instructions
            status_file = BASE_DIR / "bot_status.json"
            if status_file.exists():
                data = json.loads(status_file.read_text())
                for bot in data.get("bots", []):
                    bot_name = bot.get("name", "")
                    bot_info = {
                        "name":        bot_name,
                        "status":      bot.get("status"),
                        "last_action": bot.get("last_action", ""),
                        "zone":        bot.get("zone", ""),
                        "instruction_version": bot.get("instruction_version", 1),
                    }
                    snapshot["bots"].append(bot_info)

                    # Include current rules so Gemini can build on them
                    inst_file = INSTRUCTIONS_DIR / f"{bot_name}.json"
                    if inst_file.exists():
                        try:
                            inst = json.loads(inst_file.read_text())
                            snapshot["current_instructions"][bot_name] = {
                                "role": inst.get("role", ""),
                                "rules": inst.get("rules", []),
                                "performance_notes": inst.get("performance_notes", []),
                                "gemini_guidance": inst.get("gemini_guidance", ""),
                            }
                        except Exception:
                            pass

                snapshot["mega_iq"] = data.get("mega_iq")

            # Training corpus size
            training_file = BASE_DIR / "training.jsonl"
            if training_file.exists():
                snapshot["training_count"] = sum(1 for _ in training_file.open())

            # Memory size
            try:
                conn = sqlite3.connect(str(BASE_DIR / "memory.db"), timeout=5)
                snapshot["memory_count"] = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                conn.close()
            except Exception:
                pass

            # Arc stats
            arc_file = BASE_DIR / "status" / "arc_stats.json"
            if arc_file.exists():
                snapshot["arc_stats"] = json.loads(arc_file.read_text())

            # Recent bus activity — who's talking to whom and about what
            try:
                conn = sqlite3.connect(str(BUS_DB_PATH), timeout=5)
                rows = conn.execute(
                    "SELECT sender, recipient, payload FROM messages ORDER BY id DESC LIMIT 30"
                ).fetchall()
                conn.close()
                collab_counts = {}
                for sender, recipient, payload_str in rows:
                    try:
                        p = json.loads(payload_str)
                        msg_type = p.get("type", "unknown")
                        snapshot["bus_activity"].append(
                            f"{sender} → {recipient}: {msg_type}"
                        )
                        key = f"{sender}→{recipient}"
                        collab_counts[key] = collab_counts.get(key, 0) + 1
                    except Exception:
                        pass
                snapshot["collaboration_map"] = collab_counts
            except Exception:
                pass

            # Load per-bot configs + find evolution target
            bots_dir = BASE_DIR / "bots"
            EVOLUTION_SKIP = {"arc", "weld", "gemini_strategist"}
            if bots_dir.exists():
                bot_configs = {}
                evolution_candidates = []
                for bot_dir in bots_dir.iterdir():
                    cfg_file = bot_dir / "config.json"
                    if bot_dir.is_dir() and cfg_file.exists():
                        try:
                            cfg = json.loads(cfg_file.read_text())
                            bot_configs[bot_dir.name] = cfg
                            if bot_dir.name not in EVOLUTION_SKIP:
                                evolution_candidates.append((
                                    cfg.get("evolution_level", 0),
                                    bot_dir.name,
                                    bot_dir,
                                ))
                        except Exception:
                            pass
                if bot_configs:
                    snapshot["bot_configs"] = bot_configs

                # Pick lowest-level bot to evolve next, include its code
                if evolution_candidates:
                    evolution_candidates.sort()
                    _, evo_bot, evo_dir = evolution_candidates[0]
                    evo_code_file = evo_dir / "bot.py"
                    try:
                        evo_code = evo_code_file.read_text()
                        snapshot["evolution_target"] = {
                            "bot": evo_bot,
                            "current_level": evolution_candidates[0][0],
                            "current_code": evo_code,
                        }
                    except Exception:
                        pass

            # Load previous guidance so Gemini can track growth over time
            if GUIDANCE_FILE.exists():
                try:
                    snapshot["previous_guidance"] = json.loads(GUIDANCE_FILE.read_text())
                except Exception:
                    pass

        except Exception as e:
            self.log(f"Snapshot build error: {e}", "error")

        return snapshot

    def _call_gemini_raw(self, prompt: str) -> str:
        """Call Gemini, return raw text string. Used by story_engine."""
        try:
            body = json.dumps({"question": prompt, "use_ground_truth": False}).encode()
            req = urllib.request.Request(
                SIDEKICK_URL, data=body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
                return data.get("gemini_response", data.get("response", ""))
        except Exception:
            return self._call_gemini_direct_raw(prompt)

    def _call_gemini_direct_raw(self, prompt: str) -> str:
        """Direct Gemini API fallback, returns raw text."""
        try:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                return ""
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
            req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            return ""

    def _call_gemini(self, prompt: str) -> dict:
        """Call Gemini via the sidekick pipeline. Returns parsed JSON dict or {}."""
        try:
            body = json.dumps({"question": prompt, "use_ground_truth": False}).encode()
            req = urllib.request.Request(
                SIDEKICK_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=90) as resp:
                data = json.loads(resp.read())
                raw_response = data.get("gemini_response", data.get("response", ""))
                if not raw_response:
                    self.log("Empty Gemini response", "warning")
                    return {}
                # Parse JSON from response
                start = raw_response.find("{")
                end   = raw_response.rfind("}") + 1
                if start >= 0:
                    return json.loads(raw_response[start:end])
                self.log(f"Could not parse JSON from Gemini response: {raw_response[:200]}", "warning")
                return {}
        except urllib.error.URLError as e:
            self.log(f"Sidekick unreachable: {e} — trying direct Gemini fallback", "warning")
            return self._call_gemini_direct(prompt)
        except Exception as e:
            self.log(f"Gemini call error: {e}", "error")
            return {}

    def _call_gemini_direct(self, prompt: str) -> dict:
        """Direct Gemini API fallback if sidekick is down."""
        try:
            api_key = os.getenv("GEMINI_API_KEY", "")
            if not api_key:
                self.log("No GEMINI_API_KEY env var — cannot call Gemini directly", "error")
                return {}
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={api_key}"
            body = json.dumps({
                "contents": [{"parts": [{"text": prompt}]}]
            }).encode()
            req = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                start = text.find("{")
                end   = text.rfind("}") + 1
                if start >= 0:
                    return json.loads(text[start:end])
                return {}
        except Exception as e:
            self.log(f"Direct Gemini fallback failed: {e}", "error")
            return {}


if __name__ == "__main__":
    GeminiStrategistBot().run()
