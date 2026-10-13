"""HTTP response policy shared by private batch downloads and evidence."""
import urllib.parse
from .ui_report_html import CSP


def send_private_file(handler, payload):
    body,mime,filename = payload
    handler.send_response(200)
    handler.send_header('Content-Type',mime)
    handler.send_header('Content-Length',str(len(body)))
    handler.send_header('Content-Disposition',"attachment; filename=\"ui-report."+filename.rsplit('.',1)[-1]+"\"; filename*=UTF-8''"+urllib.parse.quote(filename,safe=''))
    handler.send_header('Cache-Control','private, no-store')
    handler.send_header('X-Content-Type-Options','nosniff')
    handler.send_header('Referrer-Policy','no-referrer')
    handler.send_header("Content-Security-Policy", "sandbox; " + CSP)
    handler.end_headers()
    handler.wfile.write(body)
