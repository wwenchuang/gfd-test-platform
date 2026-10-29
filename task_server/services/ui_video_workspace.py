"""Server-selected version-local video evidence; legacy sources remain readable."""
import os
from pathlib import Path
import re

MARKER='analysis_workspace_version'
PRIVATE_FIELDS=frozenset({MARKER,'execution_contract','video_runtime_selection','video_runtime_private',
                          'video_runtime_summary','trajectory_audit','trajectory_records','trajectory_record_index',
                          'analysis_workspace_path','trajectory_record_root','trajectory_record_path',
                          'analysis_record_path','workspace_path','source_path'})


def _version(document,expected_version):
    version=document.get('analysis_version')
    if type(version) is not int or version<=0 or type(expected_version) is not int or version!=expected_version:
        raise ValueError('video_workspace_invalid')
    return version


def _directory(path,create=False):
    if path.is_symlink():raise ValueError('video_workspace_invalid')
    if create:path.mkdir(mode=0o700,exist_ok=True)
    if not path.is_dir():raise ValueError('video_workspace_invalid')
    if hasattr(os,'geteuid') and path.stat().st_uid!=os.geteuid():raise ValueError('video_workspace_invalid')
    if create:os.chmod(path,0o700)
    return path


def _source(root,document):
    identifier=document.get('source_id')
    if not isinstance(identifier,str) or not re.fullmatch(r'uvs_[0-9a-f]{32}',identifier):
        raise ValueError('video_workspace_invalid')
    root=Path(root)
    _directory(root)
    return _directory(root/identifier)


def create_analysis_workspace(store_root,document,expected_version):
    """Return source/analysis-vN with a private frames child; never delete old work.

    Caller supplies a store document read under its owner/app guard and uses an
    expected_version transition to persist MARKER=N before exposing its media.
    This primitive does not activate the versioned path in the default worker.
    """
    version=_version(document,expected_version)
    source=_source(store_root,document)
    workspace=_directory(source/f'analysis-v{version}',create=True)
    _directory(workspace/'frames',create=True)
    return workspace


def resolve_frame_directory(store_root,document):
    source=_source(store_root,document)
    if MARKER not in document:return _directory(source/'frames')
    version=_version(document,document[MARKER])
    workspace=_directory(source/f'analysis-v{version}')
    return _directory(workspace/'frames')
