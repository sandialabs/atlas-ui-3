#!/usr/bin/env python3
"""Send one chat turn with a PDF attached, as the web UI does, and wait for it
to finish. Whether the PDF reached the model inline is read from the mock.

Exits non-zero if the turn errors or does not complete.

Usage: ws_pdf.py <backend_port> <model_key>
"""

import asyncio
import base64
import json
import sys
from io import BytesIO

import pypdf
import websockets

USER = "test@test.com"


def one_page_pdf() -> str:
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=72, height=72)
    buf = BytesIO()
    writer.write(buf)
    return base64.b64encode(buf.getvalue()).decode()


async def main() -> int:
    port, model = int(sys.argv[1]), sys.argv[2]
    async with websockets.connect(
        f"ws://127.0.0.1:{port}/ws", additional_headers={"X-User-Email": USER}
    ) as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "content": "Summarize the attached PDF.",
            "model": model,
            "selected_tools": [],
            "selected_prompts": [],
            "selected_data_sources": [],
            "user": USER,
            "files": {"brief.pdf": {"content": one_page_pdf(), "extract": False}},
            "agent_mode": False,
            "temperature": 0.7,
            "save_mode": "none",
            "incognito": True,
        }))
        while True:
            data = json.loads(await asyncio.wait_for(ws.recv(), timeout=45))
            kind = data.get("type")
            if kind == "error":
                print(f"  turn errored: {data.get('message')}")
                return 1
            if kind in ("response_complete", "chat_response"):
                return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
