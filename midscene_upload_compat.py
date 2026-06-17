"""Compatibility exports for older tests and scripts.

The production server now starts from ``task_server.app``.  This module keeps
the historical import surface without loading the legacy monolith.
"""

from task_server.app import TaskHTTPHandler as LegacyHandler
from task_server.services.sonic_service import restore_pending_sonic_suite_summary_timers
from task_server.services.report_service import start_report_cleanup_scheduler
from task_server.config import (
    ASSET_DIR,
    CASE_DIR,
    FIGMA_MAX_REFERENCE_LIMIT,
    FIGMA_PARSE_LIMIT,
    FIGMA_REFERENCE_LIMIT,
    GENERATE_JOB_DIR,
    KNOWLEDGE_DIR,
    LEARNING_DIR,
    MAX_BODY_SIZE,
    MAX_UPLOAD_BODY_SIZE,
    PORT,
    REPORT_DIR,
    TASK_DIR,
    validate_runtime_secrets,
)
from task_server.auth import verify_session_token
from task_server.storage import clean_filename, is_visible_yaml_filename
from task_server.services.yaml_service import (
    build_generation_mindmap,
    generation_artifact_filename,
    slug_for_file,
    visual_reference_message,
)
from task_server.services import yaml_service as _yaml_service
from task_server.services.knowledge_service import (
    figma_direct_node_needs_parent_lookup,
    figma_frame_candidates,
    figma_frame_to_draft,
    filter_figma_drafts_for_requirement,
)


def save_generate_job(job):
    _yaml_service.GENERATE_JOB_DIR = GENERATE_JOB_DIR
    return _yaml_service.save_generate_job(job)


def update_generate_job(job_id, **changes):
    _yaml_service.GENERATE_JOB_DIR = GENERATE_JOB_DIR
    return _yaml_service.update_generate_job(job_id, **changes)
