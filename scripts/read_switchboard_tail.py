import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(errors="replace")

base_dir = os.getcwd()
db_file = os.path.join(base_dir, 'war_room_messages.json')

with open(db_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

messages = data.get('messages', [])
last_n = 10
for msg in messages[-last_n:]:
    print(f"[{msg['id']}] {msg['from']}: {msg['content'][:200]}...")
