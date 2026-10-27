#!/usr/bin/env bash
# 从 deploy/ 目录调用一键安装
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT/scripts/install.sh" "$@"
