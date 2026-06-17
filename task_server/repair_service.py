"""AI repair decision rules.

The UI and future Agent Orchestrator should only generate YAML repair drafts
for SCRIPT_ISSUE. Other failure types must stay in human review or bug draft
flows.
"""


REPAIRABLE_FAILURE_TYPES = {"SCRIPT_ISSUE"}
NON_REPAIRABLE_FAILURE_TYPES = {"PRODUCT_BUG", "ENV_ISSUE", "UNKNOWN"}


def can_generate_yaml_repair(failure_type):
    return str(failure_type or "").upper() in REPAIRABLE_FAILURE_TYPES

