#!/usr/bin/env python3
import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MSG_FILE = ROOT / "war_room_messages.json"
JSONL_FILE = ROOT / "war_room_messages.jsonl"

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

def post_message(sender: str, content: str, channel: str = "general"):
    # 1. Update JSONL (Event Stream)
    try:
        # Get last ID
        last_id = 0
        if JSONL_FILE.exists():
            with open(JSONL_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        msg = json.loads(line)
                        last_id = max(last_id, msg.get("id", 0))
                    except: continue
        
        new_id = last_id + 1
        msg_payload = {
            "id": new_id,
            "time": utc_now_iso(),
            "from": sender,
            "from_agent_id": sender, # Using sender as agent_id for simplicity
            "to": "ALL",
            "to_agent_id": "",
            "content": content,
            "channel": channel,
            "thread_id": "",
            "message_type": "message"
        }
        
        with open(JSONL_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(msg_payload) + "\n")
        
        # 2. Update JSON (Legacy DB)
        if MSG_FILE.exists():
            with open(MSG_FILE, "r", encoding="utf-8") as f:
                db = json.load(f)
            
            if "messages" not in db:
                db["messages"] = []
            
            db["messages"].append(msg_payload)
            # Keep only last 500
            db["messages"] = db["messages"][-500:]
            
            with open(MSG_FILE, "w", encoding="utf-8") as f:
                json.dump(db, f, indent=2)
                
        print(f"Message posted successfully (ID: {new_id})")
        
    except Exception as e:
        print(f"Error posting message: {e}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Switchboard CLI Fallback")
    parser.add_argument("--sender", default="gemini-cli-alpha")
    parser.add_argument("--content", required=True)
    parser.add_argument("--channel", default="general")
    
    args = parser.parse_args()
    post_message(args.sender, args.content, args.channel)
