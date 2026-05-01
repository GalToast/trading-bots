import json
try:
    with open('war_room_messages.json', 'r', encoding='utf-8') as f:
        d = json.load(f)
    with open('reports/latest_switchboard_output.txt', 'w', encoding='utf-8') as f:
        for m in d.get('messages', [])[-20:]:
            t = m.get('time', 'unknown')
            sender = m.get('from_agent_id', 'unknown')
            content = m.get('content', '')
            f.write(f"[{t}] {sender}:\n{content}\n---\n")
except Exception as e:
    print(f"Error: {e}")
