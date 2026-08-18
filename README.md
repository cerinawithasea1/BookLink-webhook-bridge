# Booklink Bridge (notifier)

A small forwarder/bridge that watches Telegram groups, forwards media to a Booklink librarian account, captures the permanent Booklink link, and POSTs the link + metadata to a webhook.

Why use this
- Keeps your Booklink repository unchanged.
- Produces webhook events for every new file link so you can index, backup, or process files elsewhere.
- Light-weight and runs as a separate service.

Files
- notifier.py — main forwarder service.
- .env.example — environment variables.
- Dockerfile, docker-compose.yml — containerized run (includes optional consumer)
- requirements.txt — Python deps.
- consumer/ — optional webhook consumer that uploads streamed files to S3 or local disk

Quick start (local)
1. Clone the repo and checkout the branch 'add/booklink-bridge' or review the PR.

2. Copy `.env.example` -> `.env` and fill values.
   - FORWARDER_SESSION: create a StringSession for the forwarder account (see Telethon docs).
   - API_ID / API_HASH: from my.telegram.org.
   - LIBRARIAN_ID: numeric id of your Booklink librarian account (the account Booklink uses).
   - WATCH_CHATS: comma-separated chat ids or group usernames you want to monitor (or leave empty to watch everything the forwarder account can see).
   - WEBHOOK_URL: where the notifier will POST (defaults to the in-repo consumer when using compose: http://consumer:8080/notify).
   - WEBHOOK_SECRET: optional shared secret used to HMAC-sign payloads. If set, both notifier and consumer should share it.
   - For the consumer: S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY (or MINIO_ENDPOINT and credentials).

3. Build and start:
   - docker-compose up -d --build

4. Test:
   - Ensure the forwarder account is a member of the watched group(s).
   - Post a media file into a watched group; Booklink should reply to the forwarded message and the bridge will POST the /b/ link to the webhook.

Security
- Do not commit secrets into the repository. Use .env and container secrets in production.
- Use WEBHOOK_SECRET to authenticate webhook payloads.

Notes
- The bridge stores processed (chat_id, message_id) in a sqlite DB (PROCESSED_DB_PATH, default ./data/processed.db) to avoid duplicate events.
- The consumer will either upload streams to S3 (if S3_BUCKET set) or save to local /data storage.
