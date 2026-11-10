"""Fail closed before moving a temporary browser trace into private evidence."""
import base64
import io
import zipfile
import zlib

_MAX_ARCHIVE = 30 * 1024 * 1024
_MAX_UNPACKED = 80 * 1024 * 1024
_MAX_ENTRIES = 4000


def safe_trace(raw, token):
    if not isinstance(raw, bytes) or len(raw) > _MAX_ARCHIVE or not isinstance(token, str) or not token:
        return False
    secret = token.encode('utf-8')
    patterns = [secret, base64.b64encode(secret), base64.urlsafe_b64encode(secret).rstrip(b'='),
                secret.hex().encode(), token.encode('utf-16-le'), token.encode('utf-16-be')]

    def clean(value):
        return not any(pattern in value for pattern in patterns)

    if not clean(raw):
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > _MAX_ENTRIES or sum(item.file_size for item in entries) > _MAX_UNPACKED:
                return False
            remaining = _MAX_UNPACKED
            for item in entries:
                if item.flag_bits & 1 or item.is_dir():
                    if item.flag_bits & 1:
                        return False
                    continue
                if not clean(item.filename.encode('utf-8')):
                    return False
                data = archive.read(item)
                remaining -= len(data)
                if remaining < 0 or not clean(data) or data.startswith(b'PK\x03\x04'):
                    return False
                if data.startswith(b'\x1f\x8b'):
                    dec = zlib.decompressobj(16 + zlib.MAX_WBITS)
                    unpacked = dec.decompress(data, remaining + 1)
                    if len(unpacked) > remaining or not dec.eof or dec.unused_data or not clean(unpacked):
                        return False
                    remaining -= len(unpacked)
                    if unpacked.startswith((b'PK\x03\x04', b'\x1f\x8b')):
                        return False
        return True
    except (ValueError, OSError, zipfile.BadZipFile, zlib.error, RuntimeError):
        return False
