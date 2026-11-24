#!/bin/sh
# Serve the depth viewer on http://localhost:8765 (WebHID needs a secure context: localhost or file://).
# Open the printed URL in Chrome / Chromium / Edge. Ctrl+C to stop the server.
cd "$(dirname "$0")" || exit 1
echo "open http://localhost:8765/"
exec python3 -m http.server --bind 127.0.0.1 8765
