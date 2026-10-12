#!/usr/bin/env python3
from __future__ import annotations
import json
import os
import urllib.request
from pathlib import Path

APP_ID = os.environ.get("LARKSUITE_CLI_APP_ID", "").strip()
APP_SECRET = os.environ.get("LARKSUITE_CLI_APP_SECRET", "").strip()
GITHUB_ENV = os.environ.get("GITHUB_ENV", "").strip()
if not APP_ID or not APP_SECRET or not GITHUB_ENV:
    raise SystemExit("missing Feishu app credentials or GITHUB_ENV")

body = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode("utf-8")
req = urllib.request.Request(
    "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
    data=body,
    method="POST",
    headers={"Content-Type": "application/json; charset=utf-8"},
)
with urllib.request.urlopen(req, timeout=30) as resp:
    payload = json.load(resp)
if payload.get("code") != 0 or not payload.get("tenant_access_token"):
    raise SystemExit(f"Feishu token exchange failed: code={payload.get('code')} msg={payload.get('msg')}")
with Path(GITHUB_ENV).open("a", encoding="utf-8") as f:
    f.write("LARKSUITE_CLI_TENANT_ACCESS_TOKEN=" + payload["tenant_access_token"] + "\n")
print("Feishu bot token configured")
