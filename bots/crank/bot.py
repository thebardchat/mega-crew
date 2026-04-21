"""
crank.py — RIGHT FOOT zone. THE SCHEDULER.
Tracks all 16 bot next-run times from status files, staggers any simultaneous
hits, writes schedule summary to bot_status.json for dashboard visibility.
Interval: 60 sec
"""
import json
import os
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot, BOT_INTERVALS, STATUS_DIR
from knowledge import CrankKnowledge

_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
BOT_STATUS_FILE  = _MEGA / "bot_status.json"
ARC_STATS        = _MEGA / "status/arc_stats.json"
INSTRUCTIONS_DIR = _MEGA / "instructions"
GUIDANCE_FILE    = _MEGA / "status/gemini_guidance.json"
TRAINING_FILE    = _MEGA / "training.jsonl"
MEMORY_DB        = _MEGA / "memory.db"


class CrankBot(BaseBot):
    def __init__(self):
        super().__init__("crank", "rightFoot", 60)
        self.knowledge = CrankKnowledge(self)

    def tick(self):
        bots_data   = {}
        now_str     = datetime.utcnow().isoformat()
        next_runs   = {}

        all_bots = list(BOT_INTERVALS.keys()) + ["gemini_strategist"]
        for bot_name in all_bots:
            status_file = STATUS_DIR / f"{bot_name}.json"
            inst_version = 1
            inst_file = INSTRUCTIONS_DIR / f"{bot_name}.json"
            if inst_file.exists():
                try:
                    inst_version = json.loads(inst_file.read_text()).get("version", 1)
                except Exception:
                    pass

            if status_file.exists():
                try:
                    status = json.loads(status_file.read_text())
                    bots_data[bot_name] = {
                        "status":              status.get("status", "UNKNOWN"),
                        "last_run":            status.get("last_run"),
                        "last_action":         status.get("last_action", ""),
                        "zone":                status.get("zone", ""),
                        "next_run":            status.get("next_run"),
                        "instruction_version": inst_version,
                    }
                    next_runs[bot_name] = status.get("next_run", "")
                except Exception:
                    bots_data[bot_name] = {
                        "status": "UNKNOWN", "last_run": None,
                        "last_action": "status read error", "zone": "",
                        "instruction_version": inst_version,
                    }
            else:
                bots_data[bot_name] = {
                    "status": "NOT_STARTED", "last_run": None,
                    "last_action": "No status file", "zone": "",
                    "instruction_version": inst_version,
                }

        # Load arc stats for aggregated counters
        arc_stats = {}
        if ARC_STATS.exists():
            try:
                arc_stats = json.loads(ARC_STATS.read_text())
            except Exception:
                pass

        # Load MEGA IQ from spike's status
        mega_iq = 0
        spike_status = STATUS_DIR / "spike_iq.json"
        if spike_status.exists():
            try:
                mega_iq = json.loads(spike_status.read_text()).get("mega_iq", 0)
            except Exception:
                pass

        # Training corpus size
        training_count = 0
        if TRAINING_FILE.exists():
            try:
                training_count = sum(1 for _ in TRAINING_FILE.open())
            except Exception:
                pass

        # Memory row count
        memory_count = 0
        if MEMORY_DB.exists():
            try:
                import sqlite3
                conn = sqlite3.connect(str(MEMORY_DB), timeout=3)
                memory_count = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
                conn.close()
            except Exception:
                pass

        # Gemini guidance
        gemini_guidance = {}
        if GUIDANCE_FILE.exists():
            try:
                gemini_guidance = json.loads(GUIDANCE_FILE.read_text())
            except Exception:
                pass

        # Compute zone activity (bots that ran in last 10 min)
        zone_activity = {}
        now_ts = datetime.utcnow()
        for bot_name, bdata in bots_data.items():
            zone = bdata.get("zone", "unknown")
            last = bdata.get("last_run", "")
            if last:
                try:
                    from datetime import timezone
                    elapsed = (now_ts - datetime.fromisoformat(last.replace("Z",""))).total_seconds()
                    if elapsed < 600:
                        zone_activity[zone] = zone_activity.get(zone, 0) + 1
                except Exception:
                    pass

        # Write aggregated bot_status.json
        bots_list = [{"name": k, **v} for k, v in bots_data.items()]
        output = {
            "bots":                bots_list,
            "mega_iq":             mega_iq,
            "last_commit":         arc_stats.get("last_commit", None),
            "arc_approved_today":  arc_stats.get("arc_approved_today", 0),
            "arc_rejected_today":  arc_stats.get("arc_rejected_today", 0),
            "training_count":      training_count,
            "memory_count":        memory_count,
            "zone_activity":       zone_activity,
            "gemini_guidance":     gemini_guidance,
            "generated_at":        now_str,
            "timestamp":           now_str,
        }

        BOT_STATUS_FILE.write_text(json.dumps(output, indent=2))

        active = sum(1 for b in bots_list if b["status"] == "ACTIVE")
        total  = len(bots_list)
        self.log(f"Schedule updated — {active}/{total} ACTIVE — train={training_count} mem={memory_count}")
        self.heartbeat(f"{active}/{total} ACTIVE — IQ={mega_iq} train={training_count} mem={memory_count}")

        # Purge consumed bus messages older than 7 days
        try:
            from bot_base import BUS_DB
            import sqlite3 as _sql
            conn = _sql.connect(str(BUS_DB), timeout=5)
            conn.execute("DELETE FROM messages WHERE consumed=1 AND timestamp < datetime('now', '-7 days')")
            conn.commit()
            conn.close()
        except Exception:
            pass


if __name__ == "__main__":
    CrankBot().run()
