#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/../.."
mode=${1:-control}
case "$mode" in
  control) change=0 ;;
  spacing) change=1 ;;
  *) echo 'Usage: tests/week6_visual/compare.sh [control|spacing]' >&2; exit 2 ;;
esac

# The digest fixes Chromium, OS libraries and the installed Chinese fonts.
image='mcr.microsoft.com/playwright@sha256:9bd26ad900bb5e0f4dee75839e957a89ae89c2b7ab1e76050e559790e946b948'
exec docker run --rm --platform linux/amd64 --network none --shm-size=256m \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e "W06_VISUAL_SPACING_CHANGE=$change" \
  -v "$PWD:/work" -w /work "$image" \
  node node_modules/playwright/cli.js test -c tests/week6_visual/playwright.config.mjs \
  --output="test-results/week6-visual-$mode"
