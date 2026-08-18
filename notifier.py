#!/usr/bin/env python3
"""
Booklink Bridge - notifier/forwarder

- Watches WATCH_CHATS for new media messages.
- Forwards the message to LIBRARIAN_ID (Booklink's account).
- Waits for a reply from LIBRARIAN_ID that replies to the forwarded message
  and contains a Booklink /b/ token.
- Posts JSON payload to WEBHOOK_URL with metadata.

Environment variables (see .env.example):
- FORWARDER_SESSION  : Telethon StringSession for the forwarder account
- API_ID, API_HASH   : Telegram API credentials
- LIBRARIAN_ID       : numeric Telegram id of Booklink librarian account
- WATCH_CHATS        : comma-separated chat ids or usernames to watch (empty == watch all)
- WEBHOOK_URL        : URL to POST JSON payload to
- REPLY_TIMEOUT_S    : seconds to wait for librarian reply (default 30)
- PROCESSED_DB_PATH  : path to sqlite db (default ./data/processed.db)
- LINK_REGEX         : optional override regex to find Booklink link (default catches /b/<token>)
"""
import os
import asyncio
import logging
import re
import sqlite3
from pathlib import Path
from typing import Optional

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.tl.types import Message
import aiohttp

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("booklink-bridge")

API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
FORWARDER_SESSION = os.environ["FORWARDER_SESSION"]
LIBRARIAN_ID = int(os.environ["LIBRARIAN_ID"])
WATCH_CHATS = [x.strip() for x in os.environ.get("WATCH_CHATS", "").split(",") if x.strip()]
WEBHOOK_URL = os.environ["WEBHOOK_URL"]
REPLY_TIMEOUT_S = int(os.environ.get("REPLY_TIMEOUT_S", "30"))
PROCESSED_DB_PATH = os.environ.get("PROCESSED_DB_PATH", "./data/processed.db")
LINK_REGEX = os.environ.get("LINK_REGEX", r"(https?://[^\s]+/b/[A-Za-z0-9_\-]+(?:\?[^\s]+)?)")

# ensure data dir
Path(PROCESSED_DB_PATH).parent.mkdir(parents=True, exist_ok=True)

LINK_RE = re.compile(LINK_REGEX)

client = TelegramClient(StringSession(FORWARDER_SESSION), API_ID, API_HASH)


def init_db(path: str):
    conn = sqlite3.connect(path, check_same_thread=False)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS processed (
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            created_at INTEGER DEFAULT (strftime('%s','now')),
            PRIMARY KEY (chat_id, message_id)
        )
        """
    )
    conn.commit()
    return conn


DB = init_db(PROCESSED_DB_PATH)


def is_processed(chat_id: int, message_id: int) -> bool:
    cur = DB.cursor()
    cur.execute("SELECT 1 FROM processed WHERE chat_id = ? AND message_id = ? LIMIT 1", (chat_id, message_id))
    return cur.fetchone() is not None


def mark_processed(chat_id: int, message_id: int):
    cur = DB.cursor()
    try:
        cur.execute("INSERT OR IGNORE INTO processed (chat_id, message_id) VALUES (?, ?)", (chat_id, message_id))
        DB.commit()
    except Exception as e:
        log.warning("mark_processed failed: %s", e)


async def post_webhook(session: aiohttp.ClientSession, payload: dict):
    try:
        async with session.post(WEBHOOK_URL, json=payload, timeout=20) as resp:
            if resp.status >= 300:
                text = await resp.text()
                log.error("Webhook POST failed %s: %s", resp.status, text)
            else:
                log.info("Posted webhook for %s", payload.get("link"))
    except Exception as e:
        log.error("Webhook POST exception: %s", e)


async def wait_for_librarian_reply(forwarded_msg: Message, timeout: int = REPLY_TIMEOUT_S) -> Optional[str]:
    """
    Wait for the librarian to reply to the forwarded message. Return the first matched link or None.
    """
    # The forwarded message in the librarian chat will have id = forwarded_msg.id
    # Wait for a message from LIBRARIAN_ID that has reply_to_msg_id == forwarded_msg.id
    event_fut = asyncio.get_event_loop().create_future()

    def predicate(event):
        try:
            if not event.message:
                return False
            if event.sender_id != LIBRARIAN_ID:
                return False
            if getattr(event.message, "reply_to_msg_id", None) != forwarded_msg.id:
                return False
            text = event.message.message or ""
            m = LINK_RE.search(text)
            if m:
                if not event_fut.done():
                    event_fut.set_result(m.group(1))
                return True
            return False
        except Exception:
            return False

    handler = client.add_event_handler(predicate, events.NewMessage(incoming=True))

    try:
        try:
            return await asyncio.wait_for(event_fut, timeout=timeout)
        except asyncio.TimeoutError:
            return None
    finally:
        client.remove_event_handler(handler)


@client.on(events.NewMessage(incoming=True))
async def on_new_message(event: events.NewMessage.Event):
    # Only process messages with media
    if not event.message or not event.message.media:
        return

    chat_id = int(event.chat_id) if event.chat_id is not None else None
    # filter by WATCH_CHATS if provided
    if WATCH_CHATS:
        allowed = False
        if chat_id is not None and str(chat_id) in WATCH_CHATS:
            allowed = True
        else:
            try:
                username = getattr(event.chat, "username", None)
                if username and username in WATCH_CHATS:
                    allowed = True
            except Exception:
                pass
        if not allowed:
            return

    log.info("Detected media in chat %s msg=%s", chat_id, event.message.id)

    # avoid duplicate processing
    if is_processed(chat_id, event.message.id):
        log.info("Already processed %s/%s, skipping", chat_id, event.message.id)
        return

    # forward to librarian
    try:
        forwarded = await client.forward_messages(entity=LIBRARIAN_ID, messages=event.message, from_peer=event.chat_id)
        # forward_messages returns Message or list
        if isinstance(forwarded, list):
            forwarded_msg = forwarded[0]
        else:
            forwarded_msg = forwarded
        log.info("Forwarded to librarian: forwarded_msg_id=%s", forwarded_msg.id)
    except Exception as e:
        log.error("Failed to forward message: %s", e)
        return

    # wait for reply with link
    link = None
    try:
        link = await wait_for_librarian_reply(forwarded_msg, timeout=REPLY_TIMEOUT_S)
    except Exception as e:
        log.error("Error waiting for librarian reply: %s", e)

    # gather metadata
    name = None
    size = None
    doc = getattr(event.message.media, "document", None)
    if doc:
        size = getattr(doc, "size", None)
        for a in getattr(doc, "attributes", []):
            if hasattr(a, "file_name") and a.file_name:
                name = a.file_name

    payload = {
        "link": link,
        "chat_id": chat_id,
        "message_id": event.message.id,
        "filename": name,
        "size": size,
    }

    # post webhook (even if link is None so webhook consumer can react)
    async with aiohttp.ClientSession() as sess:
        await post_webhook(sess, payload)

    if link:
        mark_processed(chat_id, event.message.id)
    else:
        log.warning("No link from librarian for %s/%s", chat_id, event.message.id)


async def main():
    await client.start()
    log.info("Booklink bridge forwarder started, watching: %s", WATCH_CHATS or "ALL")
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Shutting down")
