import os
import time

import boto3
import hvac
from flask import Flask, request, jsonify, render_template_string
from werkzeug.utils import secure_filename
from botocore.config import Config as BotoConfig

VAULT_ADDR = os.getenv("VAULT_ADDR", "http://127.0.0.1:8200")
VAULT_ROLE_ID = os.environ["VAULT_ROLE_ID"]
VAULT_SECRET_ID = os.environ["VAULT_SECRET_ID"]

VAULT_AWS_CREDS_PATH = os.getenv("VAULT_AWS_CREDS_PATH", "aws/creds/s3-uploader")
VAULT_KV_MOUNT = os.getenv("VAULT_KV_MOUNT", "kv")
VAULT_KV_PATH = os.getenv("VAULT_KV_PATH", "upload-app")

# Optional if you later test LocalStack/MinIO
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")

app = Flask(__name__)

UPLOAD_FORM_HTML = """
<!doctype html>
<title>Vault + S3 Upload</title>
<h2>Upload a file</h2>
<form method="post" action="/upload" enctype="multipart/form-data">
  <input type="file" name="file" />
  <input type="submit" value="Upload" />
</form>
"""


class VaultClient:
    """
    Minimal Vault client wrapper:
    - logs in via AppRole
    - reads KV v2 config
    - reads dynamic AWS creds
    """

    def __init__(self, addr: str):
        self.client = hvac.Client(url=addr)
        self.token_expire_at = 0.0

    def login_approle(self):
        # For learning: do a simple re-login when token is missing/expired.
        # In production, you'd likely use Vault Agent for caching/renewal.
        now = time.time()
        if self.client.token and now < self.token_expire_at - 30:
            return

        resp = self.client.auth.approle.login(
            role_id=VAULT_ROLE_ID,
            secret_id=VAULT_SECRET_ID,
        )
        token = resp["auth"]["client_token"]
        lease_duration = resp["auth"].get("lease_duration", 1200)

        self.client.token = token
        self.token_expire_at = now + float(lease_duration)

    def read_app_config(self) -> dict:
        self.login_approle()
        resp = self.client.secrets.kv.v2.read_secret_version(
            mount_point=VAULT_KV_MOUNT,
            path=VAULT_KV_PATH,
            raise_on_deleted_version=True,
        )
        return resp["data"]["data"]

    def read_dynamic_aws_creds(self) -> dict:
        self.login_approle()
        resp = self.client.read(VAULT_AWS_CREDS_PATH)
        if not resp or "data" not in resp:
            raise RuntimeError(f"Failed to read AWS creds from Vault path: {VAULT_AWS_CREDS_PATH}")
        return resp["data"]


vault = VaultClient(VAULT_ADDR)


def s3_client_from_creds(creds: dict, region: str):
    session = boto3.session.Session(
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        aws_session_token=creds.get("security_token") or creds.get("session_token"),
        region_name=region,
    )
    cfg = BotoConfig(s3={"addressing_style": "path"})
    return session.client("s3", endpoint_url=S3_ENDPOINT_URL, config=cfg)


@app.get("/")
def index():
    return render_template_string(UPLOAD_FORM_HTML)


@app.post("/upload")
def upload():
    # Flask upload pattern: request.files holds uploaded files. :contentReference[oaicite:11]{index=11}
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "No file field named 'file'"}), 400

    f = request.files["file"]
    if not f or f.filename == "":
        return jsonify({"ok": False, "error": "No file selected"}), 400

    filename = secure_filename(f.filename)

    try:
        cfg = vault.read_app_config()
        bucket = cfg["bucket"]
        region = cfg.get("region", "ap-south-1")
        prefix = cfg.get("prefix", "uploads/")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Vault KV config read failed: {e}"}), 500

    try:
        aws_creds = vault.read_dynamic_aws_creds()
        s3 = s3_client_from_creds(aws_creds, region)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Vault AWS creds fetch failed: {e}"}), 500

    key = f"{prefix}{int(time.time())}-{filename}"

    try:
        data = f.read()
        # Retry up to 3 times with 5-second delays for eventual consistency
        for attempt in range(3):
            try:
                s3.put_object(Bucket=bucket, Key=key, Body=data)
                return jsonify({"ok": True, "bucket": bucket, "key": key})
            except Exception as e:
                if attempt < 2:
                    time.sleep(5)
                else:
                    raise
    except Exception as e:
        return jsonify({"ok": False, "error": f"S3 upload failed: {e}"}), 500


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
