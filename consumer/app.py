# Optional example: Flask consumer that streams Booklink links to S3 (replace with your storage)
# Requires: pip install flask requests boto3
import os
import hmac
import hashlib
from flask import Flask, request, jsonify
import requests
import boto3

app = Flask(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")

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
    signature = request.headers.get("X-Booklink-Signature", "")
    body = request.get_data()
    if WEBHOOK_SECRET:
        expected = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return jsonify({"error": "invalid signature"}), 403

    data = request.get_json()
    link = data.get("link")
    filename = data.get("filename") or f"{data.get('chat_id')}_{data.get('message_id')}.bin"
    if not link:
        return jsonify({"error": "missing link"}), 400

    # Stream from Booklink and either upload to S3 or save locally
    try:
        with requests.get(link, stream=True, timeout=60) as r:
            r.raise_for_status()
            if s3:
                s3.upload_fileobj(r.raw, S3_BUCKET, filename)
            else:
                out_dir = os.environ.get("CONSUMER_STORAGE", "/data")
                os.makedirs(out_dir, exist_ok=True)
                out_path = os.path.join(out_dir, filename)
                with open(out_path, "wb") as fh:
                    for chunk in r.iter_content(1024 * 1024):
                        if chunk:
                            fh.write(chunk)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "stored": filename}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
