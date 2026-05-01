import sys
import os
import datetime

# Add the repo root so helper scripts target the live switchboard room.
base_dir = os.getcwd()
sys.path.append(base_dir)

from comms_server import post_message, state_lock, load_state, write_state, create_message

def manual_post(sender, content, to="ALL"):
    try:
        with state_lock():
            state = load_state()
            message = create_message(
                state,
                sender=sender,
                to=to,
                content=content,
                channel="general",
                thread_id="",
                message_type="message"
            )
            write_state(state)
            print(f"Successfully posted message {message['id']} from {sender}")
    except Exception as e:
        print(f"Error posting message: {e}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python post.py <sender> <content>")
    else:
        manual_post(sys.argv[1], sys.argv[2])
