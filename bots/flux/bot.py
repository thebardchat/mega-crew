"""
flux.py — RIGHT HAND zone. Checks all 16 bot heartbeats in status/ directory.
If any bot hasn't reported in 2x its interval, logs alert and attempts restart.
Interval: 5 min
"""
import os
import json
import subprocess
from datetime import datetime
from pathlib import Path
from bot_base import BaseBot, BOT_INTERVALS, STATUS_DIR
from knowledge import FluxKnowledge


_MEGA = Path(os.environ.get("MEGA_BASE", "/mnt/shanebrain-raid/shanebrain-core/mega"))
BOTS_DIR = _MEGA / "bots"


class FluxBot(BaseBot):
    def __init__(self):
        super().__init__("flux", "rightHand", 300)
        self.knowledge = FluxKnowledge(self)

    def tick(self):
        now = datetime.utcnow()
        alerts = []
        healthy = 0

        for bot_name, interval in BOT_INTERVALS.items():
            if bot_name == "flux":
                healthy += 1
                continue

            status_file = STATUS_DIR / f"{bot_name}.json"
            if not status_file.exists():
                alerts.append(f"{bot_name}: no status file")
                continue

            try:
                status = json.loads(status_file.read_text())
                last_run_str = status.get("last_run")
                if not last_run_str:
                    alerts.append(f"{bot_name}: no last_run in status")
                    continue

                last_run = datetime.fromisoformat(last_run_str)
                elapsed  = (now - last_run).total_seconds()
                deadline = interval * 2

                if elapsed > deadline:
                    alerts.append(
                        f"{bot_name}: overdue by {int(elapsed - interval)}s "
                        f"(interval={interval}s, last_run={elapsed:.0f}s ago)"
                    )
                    self._attempt_restart(bot_name)
                else:
                    healthy += 1

            except Exception as e:
                alerts.append(f"{bot_name}: status read error — {e}")

        if alerts:
            for a in alerts:
                self.log(f"ALERT: {a}", "warning")
            self.heartbeat(f"Health: {healthy}/16 OK, {len(alerts)} alerts")
        else:
            self.heartbeat(f"All {healthy}/16 bots healthy")

    def _attempt_restart(self, bot_name: str):
        """Alert only — do NOT spawn processes. The supervisor handles restarts."""
        self.log(f"Bot {bot_name} overdue — alerting (supervisor handles restarts)")
        self.push_to_bus("crank", {
            "type": "bot_overdue_alert",
            "source": "flux",
            "bot": bot_name,
        })


if __name__ == "__main__":
    FluxBot().run()
