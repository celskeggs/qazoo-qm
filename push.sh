#!/bin/bash
set -e -u
cd "$(dirname "$0")"
rsync -av web_scripts/ /mit/cela/web_scripts/qazoo
