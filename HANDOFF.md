---
HANDOFF: BookLink Webhook Bridge — telegram.clodhost.com

Summary
- Purpose: A small bridge that watches Telegram groups, forwards media to the Booklink librarian account, waits for Booklink’s /b/ link, and POSTs metadata to a webhook. A consumer service streams Booklink links to an uploader or S3/MinIO (no large local writes).
- Repo branch: add/booklink-bridge (work-in-progress). Merge status: open PR created by owner (review & merge via GitHub UI).
- Host intended: notifier + consumer run on telegram.clodhost.com (Cloudhost, 40 GB NVMe). Use Ultra (1 TB) as remote storage/uploader endpoint to avoid filling the Cloudhost disk.
- Important constraint: Cloudhost disk is small — stream everything off-host; do not enable SHA256-mode unless safe tmp space available.

Files / places to look
- notifier.py — forwarder (Telethon) that watches chats, forwards media, waits for replies, and POSTs webhook. Supports WEBHOOK_SECRET (HMAC) and WATCH_CHATS filter.
- consumer/app.py — Flask consumer:
  - uploader-mode (UPLOADER_URL) streams to external uploader (multipart/form-data).
  - S3/MinIO mode streams to object storage (boto3.upload_fileobj).
  - Light dedupe implemented (filename+size).
  - Atomic local fallback writes (tmp -> os.replace).
  - Retry/backoff logic for uploads.
- docker-compose.yml — runs bridge + consumer.
- .env.example — all env variables.
- data/: expected Docker volume target for processed DBs:
  - notifier uses PROCESSED_DB_PATH (/data/processed.db)
  - consumer uses CONSUMER_DB_PATH (/data/files.db)

High-level flow
1. New media posted in watched chat.
2. notifier forwards message to LIBRARIAN_ID (Booklink account).
3. Notifier waits for librarian reply that is a reply-to the forwarded message and contains a /b/ link. It extracts link per LINK_REGEX.
4. Notifier POSTs JSON payload to WEBHOOK_URL (consumer). If WEBHOOK_SECRET set, it adds X-Booklink-Signature.
5. Consumer receives payload and:
   - If UPLOADER_URL set: streams the Booklink link directly to that uploader endpoint.
   - Else if S3 configured: streams to S3/MinIO with upload_fileobj.
   - Else local fallback: atomically writes file to disk (tmp->replace).
6. On success consumer records filename+size -> stored_link in files DB for light dedupe.

Quick deploy checklist (one-shot)
1. Clone & branch:
   git clone https://github.com/cerinawithasea1/BookLink-webhook-bridge.git
   cd BookLink-webhook-bridge
   git fetch origin
   git checkout add/booklink-bridge

2. Prepare .env:
   cp .env.example .env
   Edit .env and fill the required fields (DO NOT commit this file):
   - API_ID, API_HASH (my.telegram.org)
   - FORWARDER_SESSION (generate below)
   - LIBRARIAN_ID (Booklink account numeric id)
   - WATCH_CHATS (comma-separated chat ids/usernames) or leave blank
   - WEBHOOK_URL (default for compose: http://consumer:8080/notify)
   - WEBHOOK_SECRET (optional shared secret)
   - UPLOADER_URL (e.g., Cloudflare tunnel / Ultra uploader URL) OR S3_BUCKET + credentials
   - DEDUPE_MODE=light (default)
   - CONSUMER_DB_PATH=/data/files.db
   - PROCESSED_DB_PATH=/data/processed.db

3. Create StringSession (one-time):
   pip install telethon
   Run:
   python - <<'PY'
   from telethon import TelegramClient
   from telethon.sessions import StringSession
   api_id = int(input("API_ID: "))
   api_hash = input("API_HASH: ")
   with TelegramClient(StringSession(), api_id, api_hash) as client:
       print("Copy this string into FORWARDER_SESSION in .env:")
       print(client.session.save())
   PY

4. Start via Docker Compose:
   docker-compose up -d --build

5. Test end-to-end:
   - Post a media file in a watched group.
   - Watch logs:
     docker-compose logs -f bridge
     docker-compose logs -f consumer
   - Alternatively simulate webhook:
     curl -X POST -H "Content-Type: application/json" -d '{"link":"https://books.lightweave.uk/b/NTQy...","chat_id":1,"message_id":42,"filename":"test.mp3","size":12345}' http://localhost:8080/notify
     (If WEBHOOK_SECRET is set, compute HMAC and set X-Booklink-Signature.)

Important env vars (summary)
- FORWARDER_SESSION, API_ID, API_HASH, LIBRARIAN_ID
- WATCH_CHATS, LINK_REGEX (if Booklink reply format unusual)
- WEBHOOK_URL, WEBHOOK_SECRET
- UPLOADER_URL, UPLOADER_API_KEY, UPLOADER_FILE_FIELD, UPLOADER_SHARE_BASE
- S3_BUCKET, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, MINIO_ENDPOINT
- DEDUPE_MODE (none | light | sha256) — default: light
- CONSUMER_DB_PATH, PROCESSED_DB_PATH, REPLY_TIMEOUT_S
- UPLOAD_RETRIES, UPLOAD_BACKOFF_BASE
- (Optional) TELEGRAM_PROXY or WireGuard — see notes below

Safety / operations notes (don’t lose data or disk)
- Default consumer streams to UPLOADER_URL or S3. Configure one of these; do not rely on local fallback except for testing.
- Keep DEDUPE_MODE=light on Cloudhost (no heavy local disk use).
- If enabling sha256-mode later, require a safe tmp dir on the same filesystem and set a minimum free-space threshold before hashing.
- DBs:
  - processed.db prevents duplicate notifier events (by chat_id,message_id).
  - files.db records (filename,size) -> stored location for light dedupe.
  - Keep these mounted under ./data on the host (docker-compose already maps ./data:/data).

Cloudflare Tunnel / Ultra / Proxy notes
- Cloudflare Tunnel in front of your uploader is fine, but check:
  - Upload path bypasses cache, WAF, and has appropriate timeouts (CF may impose request timeouts).
  - If uploads are large/long, consider S3 presigned uploads or chunked uploads to avoid timeout/troubles.
- Ultra (1 TB) is a perfect place to host your uploader or MinIO. Point UPLOADER_URL to the Ultra endpoint (or configure MINIO endpoint).
- Telethon (notifier) uses raw TCP/MTProto. If Cloudhost cannot reach Telegram directly, use: WireGuard (recommended) or a SOCKS5 proxy (requires additional software). WireGuard is preferred if you can set it up; it needs NO code change.
- Consumer and uploader HTTP calls can be routed via HTTP_PROXY/HTTPS_PROXY by setting those env vars in docker-compose.

Troubleshooting & quick fixes
- Notifier not detecting messages:
  - Ensure forwarder account is a member of watched groups and session is valid.
  - Increase REPLY_TIMEOUT_S if Booklink replies slowly.
  - Check that Booklink actually replies to the forwarded message with a reply (reply_to_msg_id must match).
- Consumer returns "missing link":
  - Notifier didn’t extract a /b/ link; adjust LINK_REGEX in .env to match Booklink reply format.
- Uploads failing:
  - Check consumer logs for retries and HTTP status. Confirm UPLOADER_URL & auth work when you curl it directly.
  - If using Cloudflare Tunnel, test direct curl to the tunnel and check CF rules (WAF/timeouts).
- Disk full:
  - Stop containers: docker-compose down
  - Check and remove large local files or increase remote storage config to avoid local writes.
  - Remove DB files only if you know consequences (loses dedupe state).

Emergency recovery (if something breaks)
- Stop services: docker-compose down
- Clear any temporary .tmp files in /data or consumer_data if left behind (only after verifying).
- Restart: docker-compose up -d --build
- If forwarder session invalid: re-run the StringSession helper and update .env.

Open issues & permissions
- You opened an issue on Heavrnl/telegram2FileCodeBox requesting a license/permission. Until you get permission, do not copy their code into this repo — call their uploader as a separate service instead.
- The branch add/booklink-bridge contains the consumer uploader-mode; merge it when you’re ready.

Next tasks (priority order)
1) Confirm UPLOADER_URL on Ultra and set UPLOADER_API_KEY and UPLOADER_SHARE_BASE in .env (so consumer streams off-host).
2) Merge PR and deploy to telegram.clodhost.com.
3) Add /health and Prometheus counters (uploads_total, uploads_failed, dedupe_skipped) — low effort and helps monitoring.
4) Optional: WireGuard connect Cloudhost -> Ultra if Cloudhost cannot reach Telegram directly.
5) Optional: SHA256 dedupe with free-space guard (enable only when you have safe tmp space).
6) Optional: add systemd unit or run script if you want to run without Docker.

Prompts / instructions you can give Claude to continue work
- "Open the repository, checkout branch add/booklink-bridge, run a quick static check (python -m pyflakes or run linter), and list any obvious exceptions to the runtime flow."
- "Draft a minimal systemd unit for running notifier and consumer without Docker and put it in deploy/systemd/."
- "Add /health and Prometheus counters to consumer (uploads_total, uploads_failed, dedupe_skipped) and expose /metrics."
- "Add parsing for TELEGRAM_PROXY and pass proxy tuple to TelegramClient if the env variable is set."
- "Create a small test harness that posts a Booklink URL to the consumer and verifies uploader responds with status 200."

If Claude or another person interrupts or needs to hand back:
- Commit any in-progress changes to a branch named feature/hand-off-YYYYMMDD and push.
- Create a short issue describing what’s left to do and assign it to the project board. Example: “Enable metrics, add systemd units, and WireGuard docs.”
- Paste this HANDOFF.md into repo root so future pickups have context.

A short message for you to pin into the repo/Claude
- "Do not enable sha256 dedupe on telegram.clodhost.com — the host has only 40GB. Use UPLOADER_URL or S3 on Ultra (1TB) for storage. Keep DEDUPE_MODE=light. If interrupted, commit and push changes to a feature branch and create an issue describing the next action."

Contact / reference links
- Repo: https://github.com/cerinawithasea1/BookLink-webhook-bridge
- Branch: add/booklink-bridge
- Uploader repo (permission issue opened): https://github.com/Heavrnl/telegram2FileCodeBox/issues/1
- Example Booklink URL: https://books.lightweave.uk/b/NTQyMzIzODI4NDoyMDk6MQ

Last note (operational mantra)
- Priority: stream off host to Ultra or S3. Keep things small and stateless on telegram.clodhost.com. If anything looks “fun to build,” add it as a low-priority issue — don’t change the core runtime behavior until remote storage is confirmed.

If you want, I can:
- Paste this into HANDOFF.md on the branch and push it,
- Add the /health + Prometheus counters now,
- Or add Docker Compose HTTP_PROXY examples and a small WireGuard quick-config template.

Which of those would you like me to do now?
---
