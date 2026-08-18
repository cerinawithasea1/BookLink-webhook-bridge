import os
import hmac
import hashlib
from flask import Flask, request, jsonify
import requests
import boto3

app = Flask(__name__)

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

s3 = None
if S3_BUCKET:
    s3 = boto3.client(
        "s3",
        region_name=S3_REGION,
        aws_access_key_id=os.environ.get("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.environ.get("AWS_SECRET_ACCESS_KEY"),
        endpoint_url=os.environ.get("MINIO_ENDPOINT") or None,
    )


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
    if not link:
        return jsonify({"error": "missing link"}), 400

    # Attempt uploader-mode first (stream Booklink -> uploader)
    if UPLOADER_URL:
        try:
            with requests.get(link, stream=True, timeout=120) as r:
                r.raise_for_status()
                headers = {}
                if UPLOADER_API_KEY:
                    # common pattern: Authorization: Bearer <key>
                    headers["Authorization"] = f"Bearer {UPLOADER_API_KEY}"
                files = {UPLOADER_FILE_FIELD: (filename, r.raw, r.headers.get("Content-Type", "application/octet-stream"))}
                data_form = {
                    "expire_value": UPLOADER_EXPIRE_VALUE,
                    "expire_style": UPLOADER_EXPIRE_STYLE,
                }
                resp = requests.post(UPLOADER_URL, files=files, data=data_form, headers=headers, timeout=300)
                resp.raise_for_status()
                # try to parse JSON and extract a share code if present
                share_link = None
                try:
                    jr = resp.json()
                    code = jr.get("detail", {}).get("code") if isinstance(jr, dict) else None
                    if code:
                        share_link = f"{UPLOADER_SHARE_BASE}{code}"
                except Exception:
                    share_link = None
                result = {"ok": True, "stored": filename, "uploader_response_status": resp.status_code}
                if share_link:
                    result["share_link"] = share_link
                return jsonify(result), 200
        except Exception as e:
            return jsonify({"error": f"uploader error: {e}"}), 500

    # Else fallback to S3 upload if configured
    try:
        with requests.get(link, stream=True, timeout=60) as r:
            r.raise_for_status()
            if s3:
                s3.upload_fileobj(r.raw, S3_BUCKET, filename)
                return jsonify({"ok": True, "stored": filename}), 200
            else:
                out_dir = os.environ.get("CONSUMER_STORAGE", "/data")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, filename)
                with open(out_path, "wb") as fh:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            fh.write(chunk)
                return jsonify({"ok": True, "stored": out_path}), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
