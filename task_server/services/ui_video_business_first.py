"""Enrolled native hypotheses plus the existing authoritative frame pipeline."""
from .ui_video_native import validate_timeline_output


def validate_business_timeline(value, duration_ms):
    validate_timeline_output(value, duration_ms)
    scenes = value['scenes']
    if (not scenes or any(not scene['description'].strip() for scene in scenes) or
            any(a['start_ms'] > b['start_ms'] for a, b in zip(scenes, scenes[1:]))):
        raise ValueError('video_timeline_output_invalid')
    return value


def run_business_runtime(document, workspace, source_path, check, publish, *, budget):
    from .ui_video_runtime import _run_runtime
    return _run_runtime(document, workspace, source_path, check, publish, budget=budget, business=True)
