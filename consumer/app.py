import os
import hmac
import hashlib
import time
import tempfile
import sqlite3
from flask import Flask, request, jsonify, Response
import requests
import boto3
from prometheus_client import Counter, generate_latest, CONTENT_TYPE_LATEST

app = Flask(__name__)

# Prometheus metrics
UPLOADS_TOTAL = Counter('consumer_uploads_total', 'Total uploads attempted')
UPLOADS_FAILED = Counter('consumer_uploads_failed', 'Total uploads failed')
DEDUPE_SKIPPED = Counter('consumer_dedupe_skipped_total', 'Total uploads skipped due to dedupe')

# Storage / uploader configuration
S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

# Uploader-mode: stream Booklink URL to an external uploader service
UPLOADER_URL = os.environ.get("UPLOADER_URL", "")
UPLOADER_API_KEY = os.environ.get("UPLOADER_API_KEY", "")
UPLOADER_SHARE_BASE = os.environ.get("UPLOADER_SHARE_BASE", "https://domain/#/?code=")
UPLOADER_EXPIRE_STYLE = os.environ.get("UPLOADER_EXPIRE_STYLE", "day")
UPLOADER_EXPIRE_VALUE = os.environ.get("UPLOADER_EXPIRE_VALUE", "1")
UPLOADER_FILE_FIELD = os.environ.get("UPLOADER_FILE_FIELD", "file")

# Dedup + retries
DEDUPE_MODE = os.environ.get("DEDUPE_MODE", "light")  # none | light | sha256 (sha256 not implemented here)
CONSUMER_DB_PATH = os.environ.get("CONSUMER_DB_PATH", "/data/files.db")
UPLOAD_RETRIES = int(os.environ.get("UPLOAD_RETRIES", "3"))
UPLOAD_BACKOFF_BASE = float(os.environ.get("UPLOAD_BACKOFF_BASE", "2"))

s3 = None
if S3_BUCKET:
    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
    )

# Ensure data dir
os.makedirs(os.path.dirname(CONSUMER_DB_PATH) or ".", exist_ok=True)

# Initialize simple sqlite DB to track uploaded files for light dedupe
DB = sqlite3.connect(CONSUMER_DB_PATH, check_same_thread=False)
DB.execute(
    """
    CREATE TABLE IF NOT EXISTS files (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        filename TEXT,
        size INTEGER,
        stored TEXT,
        created_at INTEGER DEFAULT (strftime('%s','now'))
    )
    """
)
DB.execute("CREATE UNIQUE INDEX IF NOT EXISTS files_filename_size_idx ON files(filename, size)")
DB.commit()


def find_file_by_name_size(filename, size):
    cur = DB.cursor()
    cur.execute("SELECT stored FROM files WHERE filename = ? AND size = ? LIMIT 1", (filename, size))
    row = cur.fetchone()
    return row[0] if row else None


def record_file(filename, size, stored):
    cur = DB.cursor()
    try:
        cur.execute("INSERT OR IGNORE INTO files (filename, size, stored) VALUES (?, ?, ?)", (filename, size, stored))
        DB.commit()
    except Exception as e:
        app.logger.warning("Failed to record file metadata: %s", e)


def retry_loop(func, retries=UPLOAD_RETRIES, backoff_base=UPLOAD_BACKOFF_BASE):
    last_exc = None
    for attempt in range(1, retries + 1):
        try:
            return func()
        except Exception as e:
            last_exc = e
            wait = backoff_base ** (attempt - 1)
            app.logger.warning("Attempt %s failed: %s — retrying in %.1fs", attempt, e, wait)
            time.sleep(wait)
    raise last_exc


def atomic_write_stream(response, final_path):
    # Write stream into a tmp file in same dir and atomically replace
    dirpath = os.path.dirname(final_path) or "."
    os.makedirs(dirpath, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-", dir=dirpath)
    hasher = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as fh:
            for chunk in response.iter_content(1024 * 1024):
                if chunk:
                    fh.write(chunk)
                    hasher.update(chunk)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, final_path)
        return hasher.hexdigest()
    finally:
        if os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


@app.route("/metrics", methods=["GET"])
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"ok": True}), 200


@app.route("/notify", methods=["POST"])
def notify():
    # Verify signature if configured
    signature = request.headers.get("X-Booklink-Signature", "")
    body = request.get_data()
    if WEBHOOK_SECRET:
        expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return jsonify({"error": "invalid signature"}), 403

    data = request.get_json()
    if not data:
        return jsonify({"error": "invalid json"}), 400

    link = data.get("link")
    filename = data.get("filename") or f"{data.get('chat_id')}_{data.get('message_id')}.bin"
    size = data.get("size")

    if not link:
        return jsonify({"error": "missing link"}), 400

    # Light dedupe: check filename+size
    if DEDUPE_MODE == "light" and filename and size is not None:
        stored = find_file_by_name_size(filename, size)
        if stored:
            app.logger.info("Light dedupe: found existing file for %s (%s), returning stored=%s", filename, size, stored)
            DEDUPE_SKIPPED.inc()
            return jsonify({"ok": True, "stored": stored, "dedupe": "light"}), 200

    # Attempt uploader-mode first (stream Booklink -> uploader)
    if UPLOADER_URL:
        def do_upload():
            UPLOADS_TOTAL.inc()
            with requests.get(link, stream=True, timeout=120) as r:
                r.raise_for_status()
                headers = {}
                if UPLOADER_API_KEY:
                    headers["Authorization"] = f"Bearer {UPLOADER_API_KEY}"
                files = {UPLOADER_FILE_FIELD: (filename, r.raw, r.headers.get("Content-Type", "application/octet-stream"))}
                data_form = {
                    "expire_value": UPLOADER_EXPIRE_VALUE,
                    "expire_style": UPLOADER_EXPIRE_STYLE,
                }
                resp = requests.post(UPLOADER_URL, files=files, data=data_form, headers=headers, timeout=300)
                resp.raise_for_status()
                share_link = None
                try:
                    jr = resp.json()
                    code = jr.get("detail", {}).get("code") if isinstance(jr, dict) else None
                    if code:
                        share_link = f"{UPLOADER_SHARE_BASE}{code}"
                except Exception:
                    share_link = None
                # record file metadata
                stored_val = share_link or f"uploader:{resp.status_code}"
                record_file(filename, size, stored_val)
                return {"ok": True, "stored": stored_val, "uploader_status": resp.status_code, "share_link": share_link}

        try:
            result = retry_loop(do_upload)
            return jsonify(result), 200
        except Exception as e:
            app.logger.error("Uploader-mode failed after retries: %s", e)
            UPLOADS_FAILED.inc()
            # fall through to S3/local fallback

    # Else fallback to S3 upload if configured or local save
    def do_s3_or_local():
        UPLOADS_TOTAL.inc()
        with requests.get(link, stream=True, timeout=120) as r:
            r.raise_for_status()
            if s3:
                # retry loop will call this again if exception
                s3.upload_fileobj(r.raw, S3_BUCKET, filename)
                stored_val = f"s3://{S3_BUCKET}/{filename}"
                record_file(filename, size, stored_val)
                return {"ok": True, "stored": stored_val}
            else:
                out_dir = os.environ.get("CONSUMER_STORAGE", "/data")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, filename)
                # atomic write
                atomic_write_stream(r, out_path)
                record_file(filename, size, out_path)
                return {"ok": True, "stored": out_path}

    try:
        result = retry_loop(do_s3_or_local)
        return jsonify(result), 200
    except Exception as e:
        app.logger.error("Final upload failed after retries: %s", e)
        UPLOADS_FAILED.inc()
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
