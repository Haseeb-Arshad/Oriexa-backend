"""Monitor orchestrator execution progress for task 2280."""
import urllib.request
import json
import time
import sys


def poll(task_id=2280):
    try:
        url = f"http://localhost:8000/orchestrator/tasks/by-task/{task_id}/active"
        r = urllib.request.urlopen(url, timeout=10)
        return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


def get_preview(eid):
    try:
        url = f"http://localhost:8000/orchestrator/preview/executions/{eid}"
        r = urllib.request.urlopen(url, timeout=10)
        return json.loads(r.read())
    except Exception as e:
        return {"error": str(e)}


last_status = None
last_phase = None

print("Monitoring task 2280 orchestrator execution...")
print("=" * 60)

for i in range(120):  # 10 minutes max
    data = poll()
    if "error" in data:
        print(f"[{i*5}s] Poll error: {data['error']}")
        time.sleep(5)
        continue

    d = data.get("data", {})
    status = d.get("status", "?")
    phase = d.get("current_phase") or ""
    pct = d.get("progress_pct", 0)
    eid = d.get("execution_id", 1)

    changed = (status != last_status) or (phase != last_phase)
    if changed:
        print(f"\n[{i*5}s] STATUS={status} phase={phase} pct={pct}% eid={eid}")
        last_status = status
        last_phase = phase

        if status in ("completed", "failed", "cancelled"):
            print("\n" + "=" * 60)
            print(f"FINAL STATUS: {status}")
            preview = get_preview(eid)
            pdata = preview.get("data", {})
            print(json.dumps(pdata, indent=2)[:800])
            sys.exit(0)
    else:
        print(".", end="", flush=True)

    time.sleep(5)

print("\nTimeout — checking final state:")
data = poll()
print(json.dumps(data, indent=2))
