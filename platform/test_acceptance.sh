#!/usr/bin/env bash
# Acceptance test script for iGEM Platform MVP
# Usage: bash test_acceptance.sh
set -e

BASE_URL="${BASE_URL:-http://localhost:8000}"
EMAIL="test_$(date +%s)@example.com"
PASSWORD="testpassword123"

echo "=== 1. Register ==="
curl -sf -X POST "$BASE_URL/api/auth/register" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -m json.tool

echo "=== 2. Login ==="
TOKEN=$(curl -sf -X POST "$BASE_URL/api/auth/login" \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
echo "Token obtained: ${TOKEN:0:20}..."

echo "=== 3. Create Job (antioxidant, Tier A) ==="
JOB_RESPONSE=$(curl -sf -X POST "$BASE_URL/api/jobs" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"config":{"function_type":"antioxidant","tier":"A"}}')
echo "$JOB_RESPONSE" | python3 -m json.tool
JOB_ID=$(echo "$JOB_RESPONSE" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "Job ID: $JOB_ID"

echo "=== 4. Check Job Status ==="
curl -sf "$BASE_URL/api/jobs/$JOB_ID" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

echo "=== 5. Health check ==="
curl -sf "$BASE_URL/health" | python3 -m json.tool

echo ""
echo "✅ All checks passed. Job $JOB_ID created successfully."
echo "   Monitor: curl $BASE_URL/api/jobs/$JOB_ID -H 'Authorization: Bearer \$TOKEN'"
echo "   SSE:     curl -N $BASE_URL/api/jobs/$JOB_ID/events -H 'Authorization: Bearer \$TOKEN'"
