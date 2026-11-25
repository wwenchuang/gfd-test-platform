"""Published phone variants. A shared case keeps its ID on both platforms."""
import json
from pathlib import Path


_COUNTERPARTS = json.loads((Path(__file__).resolve().parents[2] /
                            'deploy/3d-daily/ios-counterparts.json').read_text(encoding='utf-8'))
_IOS_ORIGINS = {variant: original for original, variant in _COUNTERPARTS.items()
                if variant and variant != original}


def platform_binding(asset):
    """Return supported platforms and the original release-flow identifier."""
    case_id = asset['case_id']
    if asset.get('app_package') == 'com.kfb.model':
        if case_id in _IOS_ORIGINS:
            return ('ios',), _IOS_ORIGINS[case_id]
        if case_id in _COUNTERPARTS:
            return (('android', 'ios') if _COUNTERPARTS[case_id] == case_id else ('android',)), case_id
        if case_id.startswith('ZXB_IOS_'):
            return ('ios',), case_id
    platform = asset.get('platform')
    if platform in ('android', 'ios'):
        return (platform,), case_id
    # Older assets without a platform rely on the target compiler.
    return (('android', 'ios') if not platform else ()), case_id
