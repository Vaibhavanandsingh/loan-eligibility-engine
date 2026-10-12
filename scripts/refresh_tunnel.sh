#!/usr/bin/env bash
# Make sure the AWS Lambda can reach n8n through the Cloudflare quick tunnel.
#
#   scripts/refresh_tunnel.sh           check; repair only if something is wrong
#   scripts/refresh_tunnel.sh --force   always start a fresh tunnel
#
# A quick tunnel gets a new random address whenever it restarts, and Cloudflare
# can drop it after a network blip. This script checks that the tunnel answers
# and that processCsv is configured with its current address. If not, it starts
# a new tunnel, updates .env and n8n, and updates only the Lambda's settings.
# (Workflow R recovers uploads stuck meanwhile; this keeps the demo instant.)

set -euo pipefail
cd "$(dirname "$0")/.."

FUNCTION=loan-eligibility-engine-dev-processCsv
FORCE=${1:-}

envval() { grep -E "^$1=" .env | head -1 | cut -d= -f2- | sed 's/[[:space:]]*#.*$//'; }
export AWS_PROFILE=${AWS_PROFILE:-$(envval AWS_PROFILE)}
export AWS_REGION=${AWS_REGION:-$(envval AWS_REGION)}

say() { printf '%s\n' "$*"; }
# Empty (not an error) until the tunnel has printed its address.
tunnel_url() { docker logs loan-engine-tunnel 2>&1 | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -1 || true; }
healthy() {  # healthy <url> <tries>: n8n's /healthz answers 200 through the tunnel
  for _ in $(seq 1 "$2"); do
    [ "$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$1/healthz")" = 200 ] && return 0
    sleep 5
  done
  return 1
}
lambda_url() { aws lambda get-function-configuration --function-name "$FUNCTION" \
                 --query 'Environment.Variables.N8N_WEBHOOK_URL' --output text; }

# --- 1. Check -------------------------------------------------------------------
if [ "$FORCE" != "--force" ] && docker ps --format '{{.Names}}' | grep -qx loan-engine-tunnel; then
  url=$(tunnel_url)
  if [ -n "$url" ] && healthy "$url" 6 && [ "$(lambda_url)" = "$url/webhook/loan-match" ]; then
    say "OK: tunnel $url answers and the Lambda already uses it. Nothing to do."
    exit 0
  fi
  say "Tunnel unhealthy or Lambda out of date; repairing."
fi

# --- 2. New tunnel --------------------------------------------------------------
docker compose --profile tunnel up -d --force-recreate tunnel >/dev/null 2>&1
url=""
for _ in $(seq 1 30); do url=$(tunnel_url); [ -n "$url" ] && break; sleep 2; done
[ -n "$url" ] || { say "FAILED: the tunnel printed no address (docker logs loan-engine-tunnel)."; exit 1; }
say "New tunnel: $url"
healthy "$url" 18 || { say "FAILED: $url did not answer within 90 s."; exit 1; }

# --- 3. Point .env, n8n and the Lambda at it ---------------------------------------
python3 - "$url" <<'EOF'
import re, sys
from pathlib import Path
url = sys.argv[1]
p = Path(".env"); s = p.read_text()
for key, value in (("N8N_PUBLIC_URL", f"{url}/"), ("N8N_WEBHOOK_URL", f"{url}/webhook/loan-match")):
    line = f"{key}={value}"
    s = re.sub(rf"^{key}=.*$", line, s, flags=re.M) if re.search(rf"^{key}=", s, flags=re.M) else s + f"\n{line}\n"
p.write_text(s)
EOF
docker compose up -d n8n >/dev/null 2>&1
for _ in $(seq 1 40); do
  [ "$(docker inspect -f '{{.State.Health.Status}}' loan-engine-n8n)" = healthy ] && break; sleep 3
done
say "n8n restarted with its new public URL."

# Change only N8N_WEBHOOK_URL in the Lambda's environment: seconds instead of a
# full stack deploy. (`serverless deploy function` skips environments containing
# CloudFormation references such as DB_HOST's !GetAtt, so it cannot be used.)
# .env holds the same value, so a later `serverless deploy` keeps it.
env_json=$(aws lambda get-function-configuration --function-name "$FUNCTION" --query 'Environment' --output json \
  | python3 -c 'import json,sys; e=json.load(sys.stdin); e["Variables"]["N8N_WEBHOOK_URL"]=sys.argv[1]; print(json.dumps(e))' \
      "$url/webhook/loan-match")
aws lambda update-function-configuration --function-name "$FUNCTION" --environment "$env_json" >/dev/null
aws lambda wait function-updated --function-name "$FUNCTION"
say "Lambda settings updated."

# --- 4. Prove it ----------------------------------------------------------------
[ "$(lambda_url)" = "$url/webhook/loan-match" ] || { say "FAILED: Lambda still has $(lambda_url)"; exit 1; }
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -X POST "$url/webhook/loan-match" \
         -H 'Content-Type: application/json' -d '{}')
[ "$code" = 403 ] || { say "FAILED: webhook through the tunnel returned $code (expected 403 without the secret)."; exit 1; }
say "OK: Lambda -> $url/webhook/loan-match; the webhook answers through the tunnel and still requires the secret."
