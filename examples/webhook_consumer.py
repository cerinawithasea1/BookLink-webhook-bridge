# Optional example: Flask consumer that streams Booklink links to S3 (replace with your storage)
# Requires: pip install flask requests boto3
import os
from flask import Flask, request, jsonify
import requests
import boto3

app = Flask(__name__)

S3_BUCKET = os.environ.get("S3_BUCKET")
S3_REGION = os.environ.get("S3_REGION", "us-east-1")
s3 = boto3.client("s3", region_name=S3_REGION)

@app.route("/notify", methods=["POST"])
def notify():
    data = request.get_json()
    link = data.get("link")
    if not link:
        return jsonify({"error": "missing link"}), 400
    filename = data.get("filename") or f"{data.get('chat_id')}_{data.get('message_id')}.bin"

    try:
        with requests.get(link, stream=True, timeout=60) as r:
            r.raise_for_status()
            s3.upload_fileobj(r.raw, S3_BUCKET, filename)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

    return jsonify({"ok": True, "stored": filename}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
