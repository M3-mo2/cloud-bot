#!/usr/bin/env bash
# dev.sh — run the bot in DEV mode (mock engine optional via CLOUDBOT_MOCK_ENGINE=true).
#
# NOTE: this starts a Telegram long-poller. Only one process may poll a given
# bot token. Do NOT run this while another bot holds the token.
set -euo pipefail

cd "$(dirname "$0")/.."
PY="${PYTHON:-/home/zerops/cloud-bot-venv/bin/python}"

export CLOUDBOT_MODE="${CLOUDBOT_MODE:-dev}"
exec "$PY" -m cloudbot
