#!/usr/bin/env python3
import asyncio
import json
import os
import sys

import websockets

TOKEN = os.environ["HA_TOKEN"]
URL = "wss://ha.risserd.com/api/websocket"


async def main():
    template = sys.stdin.read()
    async with websockets.connect(URL, max_size=20 * 1024 * 1024) as ws:
        assert json.loads(await ws.recv())["type"] == "auth_required"
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        assert json.loads(await ws.recv())["type"] == "auth_ok"
        await ws.send(json.dumps({"id": 1, "type": "render_template", "template": template}))
        ack = json.loads(await ws.recv())
        if not ack.get("success"):
            print("ACK ERROR:", json.dumps(ack))
            return
        event = json.loads(await ws.recv())
        print(json.dumps(event, indent=2, ensure_ascii=False))


asyncio.run(main())
