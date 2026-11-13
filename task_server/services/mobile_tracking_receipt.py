"""Read allowlisted, redacted HTTP evidence from the published Honor workbook."""
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from zipfile import BadZipFile, ZipFile


ASSETS = Path(__file__).resolve().parents[1] / 'assets' / 'tracking'


@lru_cache(maxsize=1)
def _index():
    return json.loads((ASSETS / 'honor-receipts.json').read_text(encoding='utf-8'))


def published_receipt(request):
    if not isinstance(request, dict) or request.get('report') != 'honor':
        raise ValueError('没有此报告的已发布截图')
    receipt_id = request.get('id')
    if not isinstance(receipt_id, str) or not re.fullmatch('[a-f0-9]{16}', receipt_id):
        raise ValueError('请求编号无效')
    try:
        entry = _index().get(receipt_id)
        if not entry or not re.fullmatch(r'xl/media/image\d*\.png', entry['member']):
            raise ValueError('该请求没有已发布的逐项截图')
        with ZipFile(ASSETS / 'honor-tracking.xlsx') as workbook:
            image = workbook.read(entry['member'])
    except (OSError, BadZipFile, KeyError) as exc:
        raise ValueError('截图资源读取失败，请核对发布包') from exc
    if not image.startswith(b'\x89PNG\r\n\x1a\n') or hashlib.sha256(image).hexdigest() != entry['sha256']:
        raise ValueError('截图与已发布报告不一致，请核对发布包')
    return image
