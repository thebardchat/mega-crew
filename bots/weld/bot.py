"""
weld.py — LEFT HAND zone. Pulls Arc-approved changes. Applies them:
writes training.jsonl entries, updates persona.json fields.
ONLY bot besides Arc that touches core files, and ONLY after Arc approval.
Interval: 1 hour
"""
import os
import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot
from knowledge import WeldKnowledge


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
TRAINING_FILE    = _MEGA / "training.jsonl"
PERSONA_FILE     = _MEGA / "persona.json"
ARC_STATS        = _MEGA / "status/arc_stats.json"
INSTRUCTIONS_DIR = _MEGA / "instructions"
WELD_LOG         = _MEGA / "status/weld_log.json"


class WeldBot(BaseBot):
    def __init__(self):
        super().__init__("weld", "leftHand", 3600)
        self.knowledge = WeldKnowledge(self)

    def tick(self):
        messages = self.pull_from_bus()
        if not messages:
            self.heartbeat("No approved changes to apply")
            return

        training_written    = 0
        persona_updated     = 0
        instructions_updated = 0
        code_applied        = 0

        for msg in messages:
            payload = msg["payload"]
            if payload.get("source") != "arc" or not payload.get("arc_approved"):
                self.log(f"Skipping unapproved message from {payload.get('source','?')}", "warning")
                continue

            change_type = payload.get("original_type", "")

            # ── Apply training batch ──────────────────────────────────────────
            if change_type == "training_batch":
                entries = payload.get("entries", [])
                with open(TRAINING_FILE, "a") as f:
                    for entry in entries:
                        line = json.dumps({
                            "prompt":     entry.get("prompt", ""),
                            "response":   entry.get("response", ""),
                            "score":      entry.get("score", 0),
                            "source":     entry.get("source", "arc"),
                            "committed":  datetime.utcnow().isoformat(),
                        })
                        f.write(line + "\n")
                        training_written += 1
                self._log_weld(change_type, payload.get("source", "rivet"), f"{len(entries)} pairs", payload.get("arc_confidence", 0))
                self.log(f"Wrote {len(entries)} entries to training.jsonl")

            # ── Apply persona edit ────────────────────────────────────────────
            elif change_type == "persona_edit_proposal":
                suggestion = payload.get("suggestion", {})
                if not suggestion or "error" in suggestion:
                    continue
                try:
                    persona = json.loads(PERSONA_FILE.read_text())
                    sp = persona.get("system_prompt", "")

                    for addition in suggestion.get("proposed_additions", []):
                        if addition.strip() and addition not in sp:
                            sp += " " + addition.strip()

                    for removal in suggestion.get("proposed_removals", []):
                        if removal.strip() in sp:
                            sp = sp.replace(removal.strip(), "").strip()

                    persona["system_prompt"] = sp.strip()
                    persona["last_updated"]  = datetime.utcnow().isoformat()
                    persona["update_reason"] = suggestion.get("rationale", "Arc-approved drift fix")
                    PERSONA_FILE.write_text(json.dumps(persona, indent=2))
                    persona_updated += 1
                    rationale = suggestion.get('rationale', 'drift fix')
                    self._log_weld(change_type, "torch", rationale[:40], payload.get("arc_confidence", 0))
                    self.log(f"Applied persona edit — rationale: {rationale[:80]}")
                except Exception as e:
                    self.log(f"Persona update failed: {e}", "error")

            # ── Apply instruction update ──────────────────────────────────────
            elif change_type == "instruction_update_proposal":
                target_bot = payload.get("target_bot", "")
                field      = payload.get("field", "")
                new_value  = payload.get("new_value")
                reason     = payload.get("reason", "")
                if not target_bot or not field or new_value is None:
                    self.log("Skipping malformed instruction_update_proposal", "warning")
                    continue
                inst_file = INSTRUCTIONS_DIR / f"{target_bot}.json"
                if not inst_file.exists():
                    self.log(f"No instructions file for {target_bot}, skipping", "warning")
                    continue
                try:
                    instructions = json.loads(inst_file.read_text())
                    # Backup before writing
                    bak = INSTRUCTIONS_DIR / f"{target_bot}.json.bak"
                    bak.write_text(inst_file.read_text())
                    # Apply the update
                    instructions[field] = new_value
                    instructions["version"] = instructions.get("version", 1) + 1
                    instructions["last_updated"] = datetime.utcnow().isoformat()
                    history = instructions.get("instruction_version_history", [])
                    history.append({
                        "version": instructions["version"],
                        "field": field,
                        "reason": reason,
                        "arc_confidence": payload.get("arc_confidence", 0),
                        "ts": datetime.utcnow().isoformat(),
                    })
                    instructions["instruction_version_history"] = history[-20:]  # keep last 20
                    inst_file.write_text(json.dumps(instructions, indent=2))
                    instructions_updated += 1
                    self._log_weld(change_type, target_bot, field, payload.get("arc_confidence", 0))
                    self.log(f"Updated {target_bot} instructions: {field} (v{instructions['version']})")
                except Exception as e:
                    self.log(f"Instruction update failed for {target_bot}: {e}", "error")

            # ── Apply code change (Phase 4 self-modification) ────────────────
            elif change_type == "code_proposal":
                target_bot = payload.get("target_bot", "")
                new_code = payload.get("new_code", "")
                change_summary = payload.get("change_summary", "")
                if not target_bot or not new_code:
                    self.log("Skipping malformed code_proposal", "warning")
                    continue

                bot_dir = _MEGA / "bots" / target_bot
                bot_file = bot_dir / "bot.py"
                if not bot_file.exists():
                    self.log(f"Bot file not found: {bot_file}", "error")
                    continue

                try:
                    # Verify syntax before applying
                    compile(new_code, f"{target_bot}/bot.py", "exec")

                    # Backup current version
                    backup_dir = _MEGA / "bots" / target_bot / "backups"
                    backup_dir.mkdir(exist_ok=True)
                    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
                    shutil.copy2(str(bot_file), str(backup_dir / f"bot.py.{ts}.bak"))

                    # Write new code
                    bot_file.write_text(new_code)
                    code_applied += 1

                    # Restart the container so it picks up new code
                    container_name = f"mega-{target_bot.replace('_', '-')}"
                    restart_result = subprocess.run(
                        ["docker", "restart", container_name],
                        capture_output=True, text=True, timeout=30,
                    )
                    if restart_result.returncode == 0:
                        self.log(f"Applied code change to {target_bot} and restarted container")
                    else:
                        self.log(f"Code applied but container restart failed: {restart_result.stderr}", "warning")

                    # Increment evolution_level in bot's config
                    config_file = _MEGA / "bots" / target_bot / "config.json"
                    if config_file.exists():
                        try:
                            cfg = json.loads(config_file.read_text())
                            cfg["evolution_level"] = cfg.get("evolution_level", 0) + 1
                            cfg["last_evolved"] = datetime.utcnow().isoformat()
                            config_file.write_text(json.dumps(cfg, indent=2))
                            self.log(f"{target_bot} evolution_level → {cfg['evolution_level']}")
                        except Exception as e:
                            self.log(f"Could not update evolution_level for {target_bot}: {e}", "warning")

                    self._log_weld("code_proposal", target_bot, change_summary[:60], payload.get("arc_confidence", 0))
                    self.remember(
                        content=f"Applied code change to {target_bot}: {change_summary[:100]}",
                        memory_type="decision",
                        context=f"backup at backups/bot.py.{ts}.bak",
                        outcome="applied and restarted",
                    )
                except SyntaxError as e:
                    self.log(f"Code proposal for {target_bot} has syntax error: {e}", "error")
                except Exception as e:
                    self.log(f"Code change application failed for {target_bot}: {e}", "error")

        # Update last_commit in arc_stats
        if training_written > 0 or persona_updated > 0 or instructions_updated > 0 or code_applied > 0:
            self._update_commit_time()

        summary = f"Applied: {training_written} training, {persona_updated} persona, {instructions_updated} instructions, {code_applied} code mods"
        self.log(summary)
        self.heartbeat(summary)

    def _log_weld(self, change_type: str, target: str, field: str, confidence: float):
        log_entries = []
        if WELD_LOG.exists():
            try:
                log_entries = json.loads(WELD_LOG.read_text())
            except Exception:
                pass
        log_entries.append({
            "ts": datetime.utcnow().isoformat(),
            "change_type": change_type,
            "target": target,
            "field": field,
            "arc_confidence": confidence,
        })
        WELD_LOG.write_text(json.dumps(log_entries[-100:], indent=2))  # keep last 100

    def _update_commit_time(self):
        stats = {}
        if ARC_STATS.exists():
            try:
                stats = json.loads(ARC_STATS.read_text())
            except Exception:
                pass
        stats["last_commit"] = datetime.utcnow().isoformat()
        ARC_STATS.write_text(json.dumps(stats))


if __name__ == "__main__":
    WeldBot().run()
