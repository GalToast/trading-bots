import json

with open("war_room_messages.json", "r", encoding="utf-8") as f:
    data = json.load(f)
    messages = data.get("messages", [])[-5:]
    print("--- LATEST MESSAGES ---")
    for m in messages:
        sender = m.get("from_agent_id", m.get("from", "unknown"))
        content = m.get("content", "").replace("\n", " ")
        print(f"[{sender}] {content[:200]}")

with open("war_room_tasks.json", "r", encoding="utf-8") as f:
    data = json.load(f)
    tasks = data.get("tasks", [])
    print("\n--- OPEN TASKS ---")
    for t in tasks:
        if t.get("status") not in ("done", "closed"):
            print(f"[{t.get('id')}] {t.get('status')}")
