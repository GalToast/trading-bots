import json
import os
import sys
from datetime import datetime, timezone

messages_file = 'war_room_messages.json'
tasks_file = 'war_room_tasks.json'

def list_tasks():
    if not os.path.exists(tasks_file):
        print("Tasks file not found.")
        return
    with open(tasks_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    tasks = data.get('tasks', [])
    print("\n--- ACTIVE TASKS ---")
    for t in tasks:
        status = t.get('status', 'open')
        if status not in ['done', 'closed', 'CLOSED', 'DONE']:
            print(f"[{status}] {t.get('title')} (ID: {t.get('id')}) - Owner: {t.get('owner', 'unowned')}")

def list_messages(count=15):
    if not os.path.exists(messages_file):
        print("Messages file not found.")
        return
    with open(messages_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    msgs = data.get('messages', [])
    print(f"\n--- LAST {count} MESSAGES ---")
    for m in msgs[-count:]:
        sender = m.get('from', 'unknown')
        content = m.get('content', '')[:300]
        try:
            print(f"[{sender}]: {content}")
        except UnicodeEncodeError:
            print(f"[{sender}]: {content.encode('ascii', 'replace').decode('ascii')}")

def post_message_manual(content, sender='Antigravity', to='ALL', channel='general'):
    if not os.path.exists(messages_file):
        print("Messages file not found.")
        return
    with open(messages_file, 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    msg_id = data.get('next_message_id', 1)
    new_msg = {
        "id": msg_id,
        "time": datetime.now(timezone.utc).isoformat(),
        "from": sender,
        "from_agent_id": sender,
        "to": to,
        "to_agent_id": "",
        "content": content,
        "channel": channel,
        "thread_id": "",
        "message_type": "message",
        "receipts": []
    }
    
    data['messages'].append(new_msg)
    data['next_message_id'] = msg_id + 1
    
    with open(messages_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2)
    print(f"\n[SUCCESS] Message posted as {sender}")

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "post":
        content = " ".join(sys.argv[2:])
        post_message_manual(content)
    else:
        list_tasks()
        list_messages()
