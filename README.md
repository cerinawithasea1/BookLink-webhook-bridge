# Booklink Bridge (notifier)

A small forwarder/bridge that watches Telegram groups, forwards media to a Booklink librarian account, captures the permanent Booklink link, and POSTs the link + metadata to a webhook.

Why use this
- Keeps your Booklink repository unchanged.
- Produces webhook events for every new file link so you can index, backup, or process files elsewhere.
- Light-weight and runs as a separate service.

Files
- notifier.py — main forwarder service.
- .env.example — environment variables.
- Dockerfile, docker-compose.yml — containerized run.
- requirements.txt — Python deps.

Quick start (local)
1. Create a directory and paste these files. Copy `.env.example` -> `.env` and fill values.
2. Create a StringSession for the forwarder account:
   - Start Python REPL and use Telethon to create a StringSession:
     ```
     pip install telethon
     python -c "from telethon.sync import TelegramClient; from telethon.sessions import StringSession; print('Run helper')"
     ```
   - Alternatively see Telethon docs: https://docs.telethon.dev/en/stable/basic/stringsession.html
3. Run:
   - Local:
     ```
     pip install -r requirements.txt
     export FORWARDER_SESSION="..."
     export API_ID=...
     export API_HASH=...
     python notifier.py
     ```
   - Docker:
     ```
     docker build -t booklink-bridge .
     docker-compose up -d
     ```

Webhook payload
POST JSON:
{
  "link": "https://yourbooklink/b/xxxxx" | null,
  "chat_id": <original chat id>,
  "message_id": <original message id>,
  "filename": <optional filename from Telegram metadata>,
  "size": <optional size in bytes>
}

Notes
- The forwarder must be a member of the groups listed in WATCH_CHATS (or any groups if WATCH_CHATS empty).
- The LIBRARIAN_ID must be Booklink's account id (the account Booklink is monitoring).
- The forwarder expects Booklink to reply to the forwarded message; Booklink's reply must contain the /b/ link (default extraction regex matches that).
- The bridge stores processed message ids in a sqlite DB (PROCESSED_DB_PATH) to avoid duplicates.

Next steps / optional:
- Add authentication to the webhook endpoint (HMAC or shared secret).
- Add a webhook consumer that streams the Booklink URL into object storage (S3/MinIO) if you want permanent backups.
- Add deduplication by hashing before uploading to storage.
