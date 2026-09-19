#!/bin/zsh
# Starts a dedicated, normal Chrome profile that the scanner can attach to.
# This intentionally does not use Playwright's browser flags.
open -na "Google Chrome" --args \
  --user-data-dir="$PWD/.scanner-chrome-profile" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222
