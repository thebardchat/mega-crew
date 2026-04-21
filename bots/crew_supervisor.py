"""
crew_supervisor.py — Single entry point for the MEGA crew.
Supports two modes:
  --docker : launches via docker compose (Phase 3)
  default  : launches as subprocess workers (Phase 2)
Writes master status to bot_status.json every 30 seconds.
Managed by systemd: mega-crew.service
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

BOTS_DIR   = Path(__file__).parent
MEGA_DIR   = BOTS_DIR.parent
STATUS_DIR = MEGA_DIR / "status"
LOGS_DIR   = MEGA_DIR / "logs"
BOT_STATUS = MEGA_DIR / "bot_status.json"
DOCKER_MODE = "--docker" in sys.argv

STATUS_DIR.mkdir(exist_ok=True)
LOGS_DIR.mkdir(exist_ok=True)

# All 17 bots in launch order — crank first so it writes status immediately,
# arc early so it's ready to receive, then stagger the rest
LAUNCH_ORDER = [
    "crank",              # scheduler — goes first
    "arc",                # gatekeeper — up early
    "flux",               # health monitor — up early
    "neon",               # fast consumer (5min)
    "sparky",             # brain (10min)
    "bolt",               # pattern (10min)
    "volt",               # drift check (15min)
    "blaze",              # context injector (20min)
    "rivet",              # left hand consumer (2min)
    "torch",              # drift reactor (3min)
    "glitch",             # adversarial (30min)
    "grind",              # re-embedder (30min)
    "stomp",              # conflict scanner (1hr)
    "weld",               # applier (1hr)
    "spike",              # benchmarker (6hr)
    "forge",              # tool drafter (daily)
    "gemini_strategist",  # strategic advisor — 4x/day Gemini calls
]

STAGGER_DELAY = 3  # seconds between each bot launch


def launch_bot(name: str) -> subprocess.Popen:
    # Phase 2: each bot lives in its own directory
    bot_dir = BOTS_DIR / name
    script = bot_dir / "bot.py"
    if not script.exists():
        # Fallback to flat layout for any bot not yet migrated
        script = BOTS_DIR / f"{name}.py"
    log_file = open(LOGS_DIR / f"{name}_supervisor.log", "a")
    proc = subprocess.Popen(
        [sys.executable, str(script)],
        stdout=log_file,
        stderr=log_file,
        cwd=str(BOTS_DIR),
        env={**os.environ, "PYTHONPATH": str(BOTS_DIR)},
    )
    log_file.close()  # Popen duplicates the fd — close ours to prevent leak
    print(f"[supervisor] Launched {name} PID={proc.pid} from {script}", flush=True)
    return proc


def write_supervisor_status(processes: dict, start_time: float):
    """Write a lightweight supervisor heartbeat (crank writes the full status)."""
    try:
        running = {name: proc.poll() is None for name, proc in processes.items()}
        status = {
            "supervisor_pid": os.getpid(),
            "uptime_seconds": round(time.time() - start_time),
            "bots_running":   sum(running.values()),
            "bots_total":     17,
            "process_status": {
                name: "RUNNING" if alive else "DEAD"
                for name, alive in running.items()
            },
            "supervisor_ts":  datetime.utcnow().isoformat(),
        }
        # Merge with existing bot_status.json if present
        if BOT_STATUS.exists():
            try:
                existing = json.loads(BOT_STATUS.read_text())
                existing["supervisor"] = status
                BOT_STATUS.write_text(json.dumps(existing, indent=2))
                return
            except Exception:
                pass
        BOT_STATUS.write_text(json.dumps({"supervisor": status}, indent=2))
    except Exception as e:
        print(f"[supervisor] Status write error: {e}", flush=True)


def docker_compose_up():
    """Launch all bots via docker compose."""
    compose_file = BOTS_DIR / "docker-compose.yml"
    print(f"[supervisor] Docker mode — using {compose_file}", flush=True)
    result = subprocess.run(
        ["docker", "compose", "-f", str(compose_file), "up", "-d", "--build"],
        capture_output=True, text=True, cwd=str(BOTS_DIR),
    )
    if result.returncode != 0:
        print(f"[supervisor] docker compose up failed: {result.stderr}", flush=True)
        return False
    print(f"[supervisor] docker compose up succeeded", flush=True)
    return True


def docker_get_status() -> dict:
    """Get container status for all mega-* containers."""
    result = subprocess.run(
        ["docker", "ps", "--filter", "name=mega-", "--format", "{{.Names}}\t{{.Status}}"],
        capture_output=True, text=True,
    )
    status = {}
    for line in result.stdout.strip().split("\n"):
        if "\t" in line:
            name, state = line.split("\t", 1)
            bot_name = name.replace("mega-", "")
            status[bot_name] = "RUNNING" if "Up" in state else "DEAD"
    return status


def docker_restart_dead():
    """Restart any dead mega-* containers."""
    result = subprocess.run(
        ["docker", "ps", "-a", "--filter", "name=mega-", "--filter", "status=exited",
         "--format", "{{.Names}}"],
        capture_output=True, text=True,
    )
    for name in result.stdout.strip().split("\n"):
        if name:
            print(f"[supervisor] Restarting dead container: {name}", flush=True)
            subprocess.run(["docker", "restart", name], capture_output=True)


def write_docker_supervisor_status(start_time: float):
    """Write supervisor heartbeat in Docker mode."""
    try:
        container_status = docker_get_status()
        running = sum(1 for s in container_status.values() if s == "RUNNING")
        status = {
            "supervisor_pid": os.getpid(),
            "mode": "docker",
            "uptime_seconds": round(time.time() - start_time),
            "bots_running": running,
            "bots_total": 17,
            "process_status": container_status,
            "supervisor_ts": datetime.utcnow().isoformat(),
        }
        if BOT_STATUS.exists():
            try:
                existing = json.loads(BOT_STATUS.read_text())
                existing["supervisor"] = status
                BOT_STATUS.write_text(json.dumps(existing, indent=2))
                return
            except Exception:
                pass
        BOT_STATUS.write_text(json.dumps({"supervisor": status}, indent=2))
    except Exception as e:
        print(f"[supervisor] Docker status write error: {e}", flush=True)


def main():
    print(f"[supervisor] MEGA CREW SUPERVISOR starting — mode={'DOCKER' if DOCKER_MODE else 'SUBPROCESS'} — {datetime.utcnow().isoformat()}", flush=True)
    start_time = time.time()

    if DOCKER_MODE:
        # Docker mode: compose handles everything, we just monitor
        if not docker_compose_up():
            print("[supervisor] Failed to start docker compose — exiting", flush=True)
            sys.exit(1)

        while True:
            try:
                docker_restart_dead()
                write_docker_supervisor_status(start_time)
            except Exception as e:
                print(f"[supervisor] Docker monitor error: {e}", flush=True)
            time.sleep(30)
    else:
        # Subprocess mode (Phase 2 — current default)
        processes: dict[str, subprocess.Popen] = {}

        for name in LAUNCH_ORDER:
            bot_dir_script = BOTS_DIR / name / "bot.py"
            flat_script = BOTS_DIR / f"{name}.py"
            if not bot_dir_script.exists() and not flat_script.exists():
                print(f"[supervisor] WARNING: {name} not found (checked {name}/bot.py and {name}.py) — skipping", flush=True)
                continue
            processes[name] = launch_bot(name)
            time.sleep(STAGGER_DELAY)

        print(f"[supervisor] All {len(processes)} bots launched. Monitoring...", flush=True)

        while True:
            try:
                for name in list(processes.keys()):
                    proc = processes[name]
                    if proc.poll() is not None:
                        exit_code = proc.returncode
                        print(
                            f"[supervisor] {name} exited (code={exit_code}) — restarting in 5s",
                            flush=True,
                        )
                        time.sleep(5)
                        processes[name] = launch_bot(name)

                write_supervisor_status(processes, start_time)
            except Exception as e:
                print(f"[supervisor] Monitor loop error: {e}", flush=True)

            time.sleep(30)


if __name__ == "__main__":
    main()
