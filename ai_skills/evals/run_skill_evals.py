#!/usr/bin/env python3
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path


SKILL_NAMES = [
    "requirement_analyzer",
    "scenario_designer",
    "automation_filter",
    "visual_grounder",
    "coverage_auditor",
    "repair_patch_planner",
]


def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def fail(errors, message):
    errors.append(message)


def check_prompt_terms(root, fixture, errors):
    expected = fixture.get("expected_contract") or {}
    for skill_name, contract in expected.items():
        prompt_path = root / "prompts" / f"{skill_name}.v1.md"
        if not prompt_path.exists():
            fail(errors, f"{fixture['name']}: missing prompt {prompt_path.name}")
            continue
        prompt = read_text(prompt_path)
        for term in contract.get("must_include_prompt_terms") or []:
            if term not in prompt:
                fail(errors, f"{fixture['name']}: prompt {skill_name} missing term: {term}")


def check_schema_fields(root, fixture, errors):
    expected = fixture.get("expected_contract") or {}
    for skill_name, contract in expected.items():
        schema_path = root / "schemas" / f"{skill_name}.schema.json"
        if not schema_path.exists():
            fail(errors, f"{fixture['name']}: missing schema {schema_path.name}")
            continue
        schema = load_json(schema_path)
        required = set(schema.get("required") or [])
        properties = set((schema.get("properties") or {}).keys())
        available = required | properties
        for field in contract.get("output_fields") or []:
            if field not in available:
                fail(errors, f"{fixture['name']}: schema {skill_name} missing field: {field}")


def validate_live_payload(fixture, payload, errors):
    live = fixture.get("expected_live") or {}
    analysis = payload.get("analysis") or {}
    scenarios = payload.get("scenarios") or []
    cases = payload.get("cases") or []
    manual_cases = payload.get("manual_cases") or []
    review = payload.get("review") or {}
    matrix = analysis.get("coverage_matrix") or []

    if len(analysis.get("requirement_points") or []) < live.get("min_requirement_points", 1):
        fail(errors, f"{fixture['name']}: live output has too few requirement_points")
    if len(scenarios) < live.get("min_scenarios", 1):
        fail(errors, f"{fixture['name']}: live output has too few scenarios")
    if len(cases) < live.get("min_cases", 1):
        fail(errors, f"{fixture['name']}: live output has too few automation cases")
    if len(manual_cases) < live.get("min_manual_cases", 0):
        fail(errors, f"{fixture['name']}: live output has too few manual cases")
    if len(matrix) < live.get("min_coverage_rows", 1):
        fail(errors, f"{fixture['name']}: live output has too few coverage rows")

    text = json.dumps(payload, ensure_ascii=False)
    for term in live.get("must_include_terms") or []:
        if term not in text:
            fail(errors, f"{fixture['name']}: live output missing term: {term}")
    for term in live.get("must_not_include_terms") or []:
        if term in text:
            fail(errors, f"{fixture['name']}: live output contains forbidden term: {term}")

    readiness = review.get("requirement_readiness") or {}
    if live.get("require_readiness", True) and not readiness:
        fail(errors, f"{fixture['name']}: live output missing review.requirement_readiness")


def load_backend(project_root, ai_root):
    backend_path = project_root / "midscene-upload.py"
    os.environ["AI_SKILLS_DIR"] = str(ai_root)
    spec = importlib.util.spec_from_file_location("midscene_upload_eval", backend_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_live_eval(project_root, ai_root, fixtures, errors, report_dir):
    backend = load_backend(project_root, ai_root)
    report_dir.mkdir(parents=True, exist_ok=True)
    for fixture in fixtures:
        input_data = fixture.get("input") or {}
        payload = backend.build_cases_payload_from_skills(
            input_data.get("title") or fixture["name"],
            input_data.get("module") or "AI测试",
            [input_data.get("text_assets") or ""],
        )
        validate_live_payload(fixture, payload, errors)
        out_path = report_dir / f"{fixture['name']}.live-output.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)


def main():
    parser = argparse.ArgumentParser(description="Run Midscene AI skill contract and optional live evals.")
    parser.add_argument("--live", action="store_true", help="Call the configured Qwen/DashScope model.")
    parser.add_argument("--fixture", action="append", help="Run only fixtures whose file stem or name matches.")
    args = parser.parse_args()

    ai_root = Path(__file__).resolve().parents[1]
    project_root = ai_root.parent
    fixture_dir = ai_root / "evals" / "fixtures"
    report_dir = ai_root / "evals" / "reports"
    fixtures = []
    requested = set(args.fixture or [])
    for path in sorted(fixture_dir.glob("*.json")):
        fixture = load_json(path)
        if requested and path.stem not in requested and fixture.get("name") not in requested:
            continue
        fixtures.append(fixture)

    errors = []
    for skill_name in SKILL_NAMES:
        if not (ai_root / "prompts" / f"{skill_name}.v1.md").exists():
            fail(errors, f"missing prompt for {skill_name}")
        if not (ai_root / "schemas" / f"{skill_name}.schema.json").exists():
            fail(errors, f"missing schema for {skill_name}")

    for fixture in fixtures:
        check_prompt_terms(ai_root, fixture, errors)
        check_schema_fields(ai_root, fixture, errors)

    if args.live:
        run_live_eval(project_root, ai_root, fixtures, errors, report_dir)

    result = {
        "ok": not errors,
        "mode": "live" if args.live else "contract",
        "fixture_count": len(fixtures),
        "errors": errors,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
