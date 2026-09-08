#!/usr/bin/env python3
"""Build a minimal source upgrade bundle; never include local credentials or data."""
import argparse
import ast
import gzip
import hashlib
import io
from pathlib import Path
import tarfile


def build(destination: Path) -> Path:
    root = Path(__file__).resolve().parents[1]
    version = next(ast.literal_eval(n.value) for n in ast.parse((root / 'load_agent/__init__.py').read_text()).body
                   if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == '__version__' for t in n.targets))
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / f'midscene-load-agent-{version}-upgrade.tar.gz'
    files = sorted((root / 'load_agent').glob('*.py')) + [root / 'load_agent/Dockerfile', root / 'load_agent/requirements.txt']
    files += [root / 'deploy/load-agent' / name for name in ('check.sh','common.sh','docker-compose.yml','install.sh','uninstall.sh','upgrade.sh','.env.example')]
    files += [root / 'docs/load-agent-runtime-resource-acceptance-2026-09-08.md']
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode='w') as archive:
        for path in sorted(files):
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Unsafe or missing bundle file: {path.name}')
            content = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.size = len(content); info.mtime = 0
            info.mode = 0o755 if path.suffix == '.sh' else 0o644
            archive.addfile(info, io.BytesIO(content))
    target.write_bytes(gzip.compress(buffer.getvalue(), mtime=0))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + '.sha256').write_text(f'{digest}  {target.name}\n')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    print(build(parser.parse_args().destination))
