#!/bin/bash
set -e -u
cd "$(dirname "$0")"
rsync -av /mit/cela/web_scripts/qazoo/ web_scripts/
git status
