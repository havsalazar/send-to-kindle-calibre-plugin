#!/bin/sh
# Build send_to_kindle.zip, installable with:  calibre-customize -a send_to_kindle.zip
set -e
cd "$(dirname "$0")"

OUT=send_to_kindle.zip
FILES="plugin-import-name-send_to_kindle.txt __init__.py config.py action.py auth.py stkclient README.md"

rm -f "$OUT"
# CI runs compileall before this script, and calibre compiles the plugin itself on
# install, so byte code must not be shipped
find . -name __pycache__ -type d -prune -exec rm -rf {} +
if command -v zip >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    zip -qr "$OUT" $FILES -x '*__pycache__*' '*.pyc'
elif command -v python3 >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    python3 -m zipfile -c "$OUT" $FILES
else
    echo "Need either zip or python3 to build $OUT" >&2
    exit 1
fi

echo "Built $OUT"
echo "Install with: calibre-customize -a $OUT"
