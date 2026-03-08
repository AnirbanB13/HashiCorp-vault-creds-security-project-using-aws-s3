# 🔐 HashiCorp Vault AWS S3 Security Project

> A comprehensive proof-of-concept demonstrating secure credential management and dynamic authentication using HashiCorp Vault and AWS S3.

## 📋 Project Overview

This project demonstrates industry best practices for securing AWS credentials using **HashiCorp Vault**. It showcases how to:
- 🔑 Securely manage and rotate **AWS credentials**
- 🚀 Implement **dynamic credential generation** with automatic expiration
- 🔒 Enforce **least privilege** access patterns
- 📊 Maintain **audit trails** for compliance and traceability

### What We'll Build

A **Flask web application** that:
- ✅ Provides a simple file upload form with `/upload` endpoint
- ✅ Authenticates to **Vault** using **AppRole** (machine-to-machine authentication)
- ✅ Reads non-sensitive configuration from **Vault KV v2** (bucket/region/prefix)
- ✅ Requests temporary **AWS credentials** from Vault's AWS secrets engine
- ✅ Uploads files to **Amazon S3** with automatic credential management
- ✅ Enables **Vault audit logging** to prove credentials are never stored

---

## 🏗️ Architecture & Design Patterns

### Key Concepts

| Component | Purpose |
|-----------|---------|
| **HashiCorp Vault** | Centralized secrets and credentials management |
| **AppRole** | Machine-to-machine authentication mechanism |
| **KV v2** | Versioned secret storage for configuration |
| **AWS Secrets Engine** | Dynamic IAM credential generation and leasing |
| **Flask** | Web application framework for file uploads |
| **Boto3** | AWS SDK for Python |

### Secret Storage Architecture

```
┌─────────────────────────────────────────────────┐
│           VAULT SECURITY ARCHITECTURE           │
├─────────────────────────────────────────────────┤
│ 🔑 Vault Root Token → Admin-only configuration │
│ 🔑 AppRole RoleID/SecretID → App authentication│
│ 🔑 AWS Root Credentials → One-time setup      │
│ 🔑 Dynamic AWS Creds → Per-request, auto-expiry│
└─────────────────────────────────────────────────┘
```

### Request Flow Diagram

```
1️⃣  User uploads file to Flask web app
2️⃣  Flask authenticates to Vault via AppRole → receives token
3️⃣  Flask reads config from Vault KV v2 (bucket/region/prefix)
4️⃣  Flask requests temporary AWS credentials (aws/creds/<role>)
5️⃣  Flask uploads file to S3 using short-lived credentials
6️⃣  Credentials automatically expire (no manual revocation needed)
```

---

## 🛠️ Prerequisites

- 🐳 **Docker** (for running Vault)
- 🐍 **Python 3.10+**
- ☁️ **AWS sandbox account** with S3 bucket write permissions
- 📝 **Recommended**: Use real AWS for proper IAM/STS validation (LocalStack/MinIO won't fully validate IAM behavior)

---

## 📚 Step-by-Step Setup Guide

### **Part A: Run Vault Locally**

#### Step 1: Start Vault in Dev Mode

```bash
docker run --rm -it \
  --cap-add=IPC_LOCK \
  -p 8200:8200 \
  -e VAULT_DEV_ROOT_TOKEN_ID=root \
  -e VAULT_DEV_LISTEN_ADDRESS=0.0.0.0:8200 \
  hashicorp/vault:latest
```

#### Step 2: Configure Environment

In another terminal:

```bash
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_TOKEN="root"
```

**Why Dev Mode?**
- ✅ Fastest learning path
- ✅ Auto-unsealed (no storage backend needed)
- ✅ Perfect for local development
- ⚠️ Not production-safe

#### Step 3: Enable Audit Logging

```bash
vault audit enable file file_path=/tmp/vault_audit.log
```

**Why This Matters:**
- 📋 Proves secrets access is auditable
- 📊 Shows **AppRole login** events
- 📊 Logs **AWS credential generation** events
- 🎯 Demonstrates organizational compliance

---

### **Part B: Store App Configuration in Vault KV v2**

#### Step 4: Enable KV v2 Secrets Engine

```bash
vault secrets enable -path=kv kv-v2
```

> **Note:** KV v2 uses versioned secrets with different API paths than KV v1

#### Step 5: Write Application Configuration

```bash
vault kv put kv/upload-app \
  bucket="YOUR_BUCKET_NAME" \
  region="ap-south-1" \
  prefix="uploads/"
```

**What Goes Here?**
- ❌ NOT your AWS credentials
- ✅ Configuration that changes without redeployment (bucket, region, prefix)

---

### **Part C: Configure Vault AWS Secrets Engine**

#### Step 6: Enable AWS Secrets Engine

```bash
vault secrets enable -path=aws aws
```

#### Step 7: Configure Vault with AWS Root Credentials

```bash
# Create an AWS IAM user for Vault in your sandbox account
vault write aws/config/root \
  access_key="AKIA...." \
  secret_key="...." \
  region="ap-south-1"
```

**Why This Is Needed:**
- Vault needs AWS IAM permissions to create/mint dynamic credentials
- Vault manages IAM policies and user lifecycle
- For production, restrict this IAM user to minimum required permissions

#### Step 8: Create a Vault AWS Role

```bash
vault write aws/roles/s3-uploader \
  credential_type=iam_user \
  policy_document='{\n    "Version": "2012-10-17",\n    "Statement": [\n      {\n        "Effect":"Allow",\n        "Action":[ "s3:PutObject", "s3:AbortMultipartUpload" ],\n        "Resource":[ "arn:aws:s3:::YOUR_BUCKET_NAME/uploads/*" ]\n      }\n    ]\n  }'
```

**Test It Manually:**

```bash
vault read aws/creds/s3-uploader
```

You should see temporary `access_key` and `secret_key` returned.

---

### **Part D: AppRole Authentication (Machine-to-Machine)**

#### Step 9: Enable AppRole Auth Method

```bash
vault auth enable approle
```

#### Step 10: Create AppRole Policy

Create file `upload-app.hcl`:

```hcl
# Policy: upload-app
# Purpose: Allow Flask app minimum required access

path "kv/data/upload-app" {
  capabilities = ["read"]
}

path "aws/creds/s3-uploader" {
  capabilities = ["read"]
}
```

Write the policy:

```bash
vault policy write upload-app upload-app.hcl
```

**Why This Is "Rigid" (Least Privilege):**
- ✅ App can only READ config and credentials
- ❌ Cannot LIST secrets
- ❌ Cannot READ other paths
- ❌ Cannot ADMINISTER Vault

#### Step 11: Create AppRole Instance

```bash
vault write auth/approle/role/upload-app \
  token_policies="upload-app" \
  token_ttl="20m" \
  token_max_ttl="1h"
```

**Obtain Credentials:**

```bash
vault read auth/approle/role/upload-app/role-id
vault write -f auth/approle/role/upload-app/secret-id
```

Save these values for application use.

---

### **Part E: Build the Flask Application**

#### Step 12: Create Python Environment

```bash
mkdir flask-vault-s3-uploader && cd flask-vault-s3-uploader
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\\Scripts\\activate
pip install flask hvac boto3 werkzeug
```

#### Step 13: Create app.py

(See app.py in repository for full implementation)

---

### **Part F: Run the Application**

#### Step 14: Export AppRole Credentials

```bash
export VAULT_ADDR="http://127.0.0.1:8200"
export VAULT_ROLE_ID="(RoleID from Step 11)"
export VAULT_SECRET_ID="(SecretID from Step 11)"
```

#### Step 15: Launch Flask

```bash
python app.py
```

Open in browser: `http://127.0.0.1:8000/`

Upload a file and confirm it appears in S3 under `uploads/` prefix.

---

## ✅ Organizational Evaluation Checklist

Demonstrate these security capabilities to stakeholders:

### 1️⃣ Prove "No Static AWS Keys in App"

```bash
grep -r "AKIA" .  # Should return NOTHING
```

**Why It Matters:**
- ✅ Credentials are never hardcoded
- ✅ AppRole RoleID/SecretID injected via environment variables
- ✅ Can later use Vault Agent, Kubernetes, or HashiCorp Cloud Platform

### 2️⃣ Demonstrate Least Privilege

**Vault Policy:**
- ✅ Read-only access to KV v2 config path
- ✅ Read-only access to specific AWS role credentials
- ❌ No ability to list or modify other secrets

**AWS Policy:**
- ✅ S3 `PutObject` permission restricted to `bucket/uploads/*`
- ✅ No wildcard permissions
- ✅ Time-limited credentials (default 1 hour)

### 3️⃣ Audit Trail & Traceability

View audit logs:

```bash
tail -f /tmp/vault_audit.log | jq .
```

You'll see:
- 📋 AppRole login events
- 📋 KV secret reads
- 📋 AWS credential generation events
- 📋 User/app identity on each action

### 4️⃣ Rotation & TTL Testing

- Test automatic credential expiration
- Simulate failed credential reuse
- Plan for rotation strategy
- Consider Vault Agent for production deployments

---

## ⚠️ Common Gotchas & Troubleshooting

| Issue | Solution |
|-------|----------|
| **KV v2 path mismatch** | CLI: `vault kv get kv/upload-app` → Policy: `path "kv/data/upload-app"` |
| **S3 upload fails** | Verify IAM policy Resource ARN matches your prefix (e.g., `uploads/*`) |
| **Dev Vault data lost** | Dev mode is in-memory; restarting container resets all data |
| **AppRole auth fails** | Verify RoleID/SecretID via: `vault read auth/approle/role/upload-app/role-id` |
| **S3 LocalStack issues** | Use real AWS for proper IAM/STS validation during testing |

---

## 🚀 Next Steps (Production Readiness)

- 🔒 Deploy Vault with persistent storage backend (Consul, S3)
- 🤖 Implement **Vault Agent** to auto-renew tokens
- 🐳 Integrate with **Kubernetes** for automatic credential injection
- 📈 Set up **auto-unsealing** (AWS KMS, Azure Key Vault)
- 🔄 Implement credential rotation policies
- 📊 Monitor and alert on Vault audit logs

---

Screenshots:
<img width="1690" height="1024" alt="image" src="https://github.com/user-attachments/assets/f7ab6e52-7006-49e7-a092-6a3664b3f527" />
<img width="1880" height="711" alt="image" src="https://github.com/user-attachments/assets/4adbcf21-715c-4556-b7cd-62584a1978d9" />
<img width="425" height="218" alt="image" src="https://github.com/user-attachments/assets/3e24f107-a930-4be3-9c1b-d72e1affd15d" />

---

## 📚 References

- [HashiCorp Vault Documentation](https://www.vaultproject.io/docs)
- [AppRole Auth Method](https://www.vaultproject.io/docs/auth/approle)
- [AWS Secrets Engine](https://www.vaultproject.io/docs/secrets/aws)
- [KV v2 Secrets Engine](https://www.vaultproject.io/docs/secrets/kv/kv-v2)
- [Flask File Upload](https://flask.palletsprojects.com/en/latest/patterns/fileuploads/)
- [Boto3 S3 Documentation](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/s3.html)

---

**Made with ❤️ for secure credential management**
