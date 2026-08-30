#!/usr/bin/env bash
# Start the whole backend: gate (8000), merchant (8100), webhook receiver (8110).
#
# With no Razorpay credentials it runs on the mock_upi rail, which exercises
# every line above the adapter. Set RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET (test
# keys only) and PACT_RAIL=razorpay to settle for real.
set -euo pipefail
cd "$(dirname "$0")/.."

export PACT_PROFILE="${PACT_PROFILE:-razorpay-track01}"
export PACT_DB_URL="${PACT_DB_URL:-sqlite:///./pact.db}"
PY=.venv/bin/python

trap 'kill 0' EXIT

$PY -m uvicorn core.app:app --port 8000 --log-level warning &
$PY -m uvicorn merchant.app:app --port 8100 --log-level warning &
$PY -m uvicorn rails.razorpay.webhook_app:app --port 8110 --log-level warning &

echo "gate      http://localhost:8000/v1/health"
echo "merchant  http://localhost:8100/v1/health"
echo "webhooks  http://localhost:8110/v1/health"
wait
