# HashiCorp-vault-creds-security-project-using-aws-s3
This project/POC essentially uses HashiCorp Vault to secure, manage, rotate AWS creds while automatically authenticating to AWS s3 to store objects.

What we’ll build locally:

A Flask web app with a simple upload form + /upload endpoint.

The app authenticates to Vault using AppRole (machine-to-machine auth).

The app reads non-secret config from Vault KV v2 (bucket/region/prefix).

The app asks Vault’s AWS secrets engine for dynamic, short-lived AWS credentials and uploads to S3.

Vault audit logging enabled so you can prove to your org: “this app never stores AWS keys”.

This matches the “real world” pattern: dynamic creds with TTL + least privilege + audit trails. Vault’s AWS engine is designed for dynamic IAM creds and leasing. (HashiCorp Developer)
AppRole is the standard “apps/services authenticate to Vault” method. (HashiCorp Developer)
KV v2 is the versioned secret store that most orgs use for config/secrets. (HashiCorp Developer)
Flask file upload pattern uses request.files and multipart forms. (flask.palletsprojects.com)

Architecture (mental model)
What secrets exist where
Vault root token: only for you locally to configure Vault.

Vault AppRole RoleID/SecretID: used by the app to get a Vault token (like “username/password” for a machine). (HashiCorp Developer)

AWS root creds configured in Vault: one-time setup (Vault uses this to create short-lived IAM creds).

Dynamic AWS creds: generated per request (or cached) and expire automatically (leases). (HashiCorp Developer)

Request flow
User uploads a file to Flask.

Flask logs in to Vault via AppRole → receives a Vault token.

Flask reads config from KV v2 (bucket/region/prefix).

Flask reads aws/creds/<role> → gets temporary AWS keys.

Flask uploads bytes to S3 using boto3.

Local Step-by-step Setup
Prereqs
Docker

Python 3.10+

AWS sandbox account + an S3 bucket you can write to (recommended for realistic IAM testing)

Note: “Local S3” (LocalStack/MinIO) is okay for dev flow, but it won’t fully validate IAM/STS behavior the way AWS does. For a real evaluation, Vault should mint creds against real AWS IAM.

Part A — Run Vault locally
1) Start Vault dev server (Docker)
docker run --rm -it \
  --cap-add=IPC_LOCK \
  -p 8200:8200 \
  -e VAULT_DEV_ROOT_TOKEN_ID=root \
  -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 \
  hashicorp/vault:latest
In another terminal:

export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="root"
Why dev mode?

Easiest learning path.

Auto-unsealed, no storage backend setup.

Not production-safe, but perfect for local.

2) Enable audit logging (so you can show evidence)
vault audit enable file file_path=/tmp/vault_audit.log
Why this matters in orgs

“Prove” secrets access is auditable.

You’ll see AppRole login + AWS creds generation events.

Part B — Store app config in Vault KV v2
3) Enable KV v2
vault secrets enable -path=kv kv-v2
KV v2 has versioned secrets and different API paths than KV v1. (HashiCorp Developer)

4) Write upload app config
vault kv put kv/upload-app \
  bucket="YOUR_BUCKET_NAME" \
  region="ap-south-1" \
  prefix="uploads/"
What goes here?

Not your AWS credentials.

Things you might change without redeploying: bucket, region, object prefix.

Part C — Configure Vault AWS Secrets Engine (dynamic credentials)
5) Enable AWS secrets engine
vault secrets enable -path=aws aws
6) Configure Vault with AWS “root” credentials
Create an AWS IAM user for Vault (in your sandbox account), generate access keys, then:

vault write aws/config/root \
  access_key="AKIA...." \
  secret_key="...." \
  region="ap-south-1"
Why this is needed
Vault needs AWS permissions to create/attach policies for dynamic users/roles and mint keys. (HashiCorp Developer)

For a stricter org-ready setup, you’d constrain this IAM user to only what Vault needs (IAM user creation, policy attachment, etc.). Start broad, then tighten.

7) Create a Vault AWS role that can only upload to one prefix
This role defines what Vault-generated credentials are allowed to do.

vault write aws/roles/s3-uploader \
  credential_type=iam_user \
  policy_document='{
    "Version": "2012-10-17",
    "Statement": [
      {
        "Effect":"Allow",
        "Action":[ "s3:PutObject", "s3:AbortMultipartUpload" ],
        "Resource":[ "arn:aws:s3:::YOUR_BUCKET_NAME/uploads/*" ]
      }
    ]
  }'
When the app reads aws/creds/s3-uploader, Vault generates credentials under that policy. (HashiCorp Developer)

Test it manually
vault read aws/creds/s3-uploader
You should see access_key and secret_key returned.

Part D — App authentication via AppRole (machine auth)
8) Enable AppRole
vault auth enable approle
AppRole is designed for machine-to-machine authentication. (docs.devnet-academy.com)

9) Create a policy for the Flask app
Create a file upload-app.hcl:

path "kv/data/upload-app" {
  capabilities = ["read"]
}

path "aws/creds/s3-uploader" {
  capabilities = ["read"]
}
Write policy:

vault policy write upload-app upload-app.hcl
Why this is “rigid”

The app can only read exactly what it needs (config + one creds endpoint).

It cannot list secrets, cannot read other paths, cannot administer Vault.

10) Create AppRole bound to that policy
vault write auth/approle/role/upload-app \
  token_policies="upload-app" \
  token_ttl="20m" \
  token_max_ttl="1h"
Now obtain RoleID + SecretID:

vault read auth/approle/role/upload-app/role-id
vault write -f auth/approle/role/upload-app/secret-id
RoleID/SecretID are used during login to get a Vault token. (HashiCorp Developer)

Part E — Build the Flask app
11) Create Python environment
mkdir flask-vault-s3-uploader && cd flask-vault-s3-uploader
python -m venv .venv
source .venv/bin/activate
pip install flask hvac boto3 werkzeug
12) Create app.py
Part F — Run it
13) Export AppRole values for the app
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_ROLE_ID="(RoleID from step 10)"
export VAULT_SECRET_ID="(SecretID from step 10)"
Run Flask:

python app.py
Open in browser:

http://127.0.0.1:8000/

Upload a file and confirm it appears in S3 under uploads/.

Part G — “Org-rigid” evaluation checklist (what to demonstrate)
1) Prove “no static AWS keys in app”
Search your project for AKIA — should be none.

AppRole values can be injected via env vars (or later via Vault Agent, Kubernetes, etc.)

2) Least privilege
Your Vault policy only allows:

kv/data/upload-app read

aws/creds/s3-uploader read

Your AWS policy only allows:

PutObject to bucket/uploads/*

This is the “two-layer least privilege” story orgs like.

3) Auditing and traceability
Open audit log:

tail -f /tmp/vault_audit.log
Upload a file and you’ll see:

AppRole login

KV read

aws/creds read

4) Rotation / TTL testing (the real learning)
Make AWS creds “short lived” by switching to STS-based patterns later (or by fetching per upload as we do now).

Validate failure modes: what happens if you try to reuse expired creds?

(Next step for “rigid”: add Vault Agent and stop embedding RoleID/SecretID directly into env vars.)

Common gotchas (so you don’t lose time)
KV v2 path mismatch: CLI vault kv get kv/upload-app maps to API path kv/data/upload-app. Your policy must reference kv/data/... for KV v2. (HashiCorp Developer)

S3 prefix in IAM policy must match what your app writes (e.g., uploads/*).

Dev Vault is in-memory-ish: restarting dev server resets everything. For an org-style demo, you’ll likely move to file storage + init/unseal steps later.
