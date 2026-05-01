#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

import anyio
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "comms_server.py"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the local switchboard MCP server over stdio.")
    parser.add_argument("--server", default=str(SERVER))
    parser.add_argument("--agent-id", default="switchboard-probe")
    parser.add_argument("--nickname", default="Switchboard Probe")
    parser.add_argument("--tag", default="switchboard-probe")
    parser.add_argument("--channel", default="general")
    parser.add_argument("--after-id", type=int, default=0)
    parser.add_argument("--post-message", default="")
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--stderr-log", default=str(ROOT / "reports" / "switchboard" / "probe_stderr.log"))
    parser.add_argument("--unbuffered", action="store_true", default=False)
    return parser.parse_args()


async def with_timeout(label: str, timeout_seconds: float, coro):
    with anyio.fail_after(timeout_seconds):
        return await coro


async def run_probe(args: argparse.Namespace) -> dict:
    server_args = []
    if args.unbuffered:
        server_args.append("-u")
    server_args.append(str(Path(args.server).resolve()))
    server = StdioServerParameters(
        command=sys.executable,
        args=server_args,
        cwd=str(ROOT),
    )
    stderr_path = Path(args.stderr_log).resolve()
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    with stderr_path.open("a", encoding="utf-8") as errlog:
        errlog.write(
            f"\n=== probe start pid={os.getpid()} command={server.command} "
            f"args={' '.join(server.args)} ===\n"
        )
        errlog.flush()
        async with stdio_client(server, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await with_timeout("initialize", args.timeout_seconds, session.initialize())
                tools_result = await with_timeout("list_tools", args.timeout_seconds, session.list_tools())
                tool_names = [getattr(tool, "name", "") for tool in getattr(tools_result, "tools", []) or []]

                join_result = await with_timeout(
                    "join_chat",
                    args.timeout_seconds,
                    session.call_tool(
                        "join_chat",
                        {
                            "harness": "codex-probe",
                            "agent_id": args.agent_id,
                            "nickname": args.nickname,
                            "requested_tag": args.tag,
                            "description": "Switchboard MCP health probe",
                            "capabilities": "probe",
                        },
                    ),
                )
                channel_result = await with_timeout(
                    "read_channel",
                    args.timeout_seconds,
                    session.call_tool(
                        "read_channel",
                        {
                            "channel": args.channel,
                            "after_id": int(args.after_id),
                        },
                    ),
                )
                post_result = None
                if args.post_message:
                    post_result = await with_timeout(
                        "post_message",
                        args.timeout_seconds,
                        session.call_tool(
                            "post_message",
                            {
                                "sender": args.agent_id,
                                "channel": args.channel,
                                "message_type": "probe",
                                "content": args.post_message,
                            },
                        ),
                    )

    return {
        "ok": True,
        "stderr_log": str(stderr_path),
        "tools": tool_names,
        "join_result": getattr(join_result, "structuredContent", None),
        "read_channel_result": getattr(channel_result, "structuredContent", None),
        "post_result": getattr(post_result, "structuredContent", None) if post_result else None,
    }


def main() -> int:
    args = parse_args()
    try:
        result = anyio.run(run_probe, args)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except BaseException as exc:
        payload = {
            "ok": False,
            "error_type": type(exc).__name__,
            "error": str(exc),
            "stderr_log": str(Path(args.stderr_log).resolve()),
            "traceback": traceback.format_exc(),
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
