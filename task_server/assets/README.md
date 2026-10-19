# Midscene replay renderer

`midscene-report-1.13.0.html.gz` is the gzip-compressed `REPORT_HTML_TEMPLATE` from the installed `@midscene/core` 1.13.0 distribution (`dist/lib/report-html-template.js`), with the remote favicon link removed. License: `MIDSCENE-LICENSE` (MIT). It contains no execution data. The three bundled renderer scripts are fixed application assets, never copied from uploaded reports at request time.

Only validated inert JSON and embedded raster screenshots are inserted by `ui_report_replay.py`. The rendered document uses a restrictive CSP inside an opaque-origin `sandbox="allow-scripts"` iframe. A per-iframe in-memory Storage shim supplies theme preferences without access to platform storage. History state updates omit URL changes because the isolated srcdoc has no shareable address. Keep the template version pinned and rerun real-browser playback/opacity checks when upgrading.
