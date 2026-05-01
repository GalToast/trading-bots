import json
from pathlib import Path

path = Path("war_room_messages.json")
if path.exists():
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    print(f"Keys: {list(data.keys())}")
    print(f"Messages: {len(data.get('messages', []))}")
    print(f"Agents: {len(data.get('agents', {}))}")
else:
    print("File not found")
