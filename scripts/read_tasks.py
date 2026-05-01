import json

with open("war_room_tasks.json", "r", encoding="utf-8") as f:
    data = json.load(f)
    tasks = data.get("tasks", [])
    print("\n--- OPEN TASKS ---")
    for t in tasks:
        if t.get("status") not in ("done", "closed", "completed"):
            print(f"[{t.get('id')}] {t.get('status')} - {t.get('title')}")
