import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from task_server.services.ai_skill_service import extract_failure_brief
from task_server.services import repair_service


NODE_DEPENDENCY_ERROR = (
    "Error: Cannot find module './db.json'\n"
    "Require stack:\n"
    "- C:\\Users\\gfd\\AppData\\Roaming\\npm\\node_modules\\@midscene\\cli\\node_modules\\mime-db\\index.js\n"
)


class RunnerDependencyFailureTest(unittest.TestCase):
    def test_wait_failure_review_uses_execution_evidence_before_yaml_cleanup_guard(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "module"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(
                "android: {}\ntasks:\n- name: test\n  flow:\n  - launch: com.kfb.model\n  - aiWaitFor: 首页内容可见\n    timeout: 12000\n",
                encoding="utf-8",
            )
            job = {
                "module": "module", "file": "case.yaml",
                "attempts": [{"teardown": {"ok": True, "message": "force-stopped com.kfb.model"}}],
            }
            classified = {
                "category": "script_issue", "failure_type": "wait_strategy", "confidence": 0.8,
                "reason": "首页内容等待条件需要复核", "evidence": ["aiWaitFor 首页内容可见 timed out"],
                "suggested_action": "核对目标页面", "can_auto_repair": False,
            }
            with patch.object(repair_service, "TASK_DIR", temp_dir), patch.object(
                repair_service, "dashscope_api_key", return_value="test"
            ), patch.object(repair_service, "classify_failure_by_context", return_value=classified):
                review = repair_service.call_dashscope_failure_review(
                    job, "aiWaitFor 首页内容可见 timed out", "", None
                )
        self.assertEqual(review["failure_type"], "wait_strategy")
        self.assertNotIn("缺少后置关闭 App", review["reason"])

    def test_model_service_failure_is_not_overridden_by_missing_terminate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "module"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(
                "android: {}\ntasks:\n- name: test\n  flow:\n  - launch: com.kfb.model\n  - aiWaitFor: 首页内容可见\n",
                encoding="utf-8",
            )
            job = {"module": "module", "file": "case.yaml"}
            with patch.object(repair_service, "TASK_DIR", temp_dir), patch.object(
                repair_service, "dashscope_api_key", return_value="test"
            ):
                review = repair_service.call_dashscope_failure_review(
                    job, "AI call error: Request was aborted", "", None
                )
        self.assertEqual(review["category"], "env_issue")
        self.assertEqual(review["failure_type"], "model_service")
        self.assertFalse(review["can_auto_repair"])

    def test_yaml_cleanup_guard_is_only_a_static_fallback(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "module"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(
                "android: {}\ntasks:\n- name: test\n  flow:\n  - launch: com.kfb.model\n  - aiTap: 我的\n",
                encoding="utf-8",
            )
            job = {"module": "module", "file": "case.yaml"}
            with patch.object(repair_service, "TASK_DIR", temp_dir), patch.object(
                repair_service, "dashscope_api_key", return_value="test"
            ):
                review = repair_service.call_dashscope_failure_review(job, "", "", None)
        self.assertEqual(review["category"], "script_issue")
        self.assertEqual(review["reason"], "缺少后置关闭 App")

    def test_missing_node_dependency_is_environment_failure(self):
        brief = extract_failure_brief(stdout=NODE_DEPENDENCY_ERROR)
        self.assertEqual(brief["failure_type"], "runtime_dependency")
        self.assertFalse(brief["repair_plan"]["can_repair_yaml"])
        self.assertEqual(
            extract_failure_brief(stdout="Failed to locate element: 打印记录")["failure_type"],
            "element_not_found",
        )

    def test_dependency_failure_takes_priority_over_missing_terminate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            module_dir = Path(temp_dir) / "module"
            module_dir.mkdir()
            (module_dir / "case.yaml").write_text(
                "android: {}\ntasks:\n- name: test\n  flow:\n  - launch: com.kfb.model\n  - aiTap: 我的\n",
                encoding="utf-8",
            )
            job = {"module": "module", "file": "case.yaml"}
            with patch.object(repair_service, "TASK_DIR", temp_dir), patch.object(
                repair_service, "dashscope_api_key", return_value="test"
            ):
                review = repair_service.call_dashscope_failure_review(
                    job, NODE_DEPENDENCY_ERROR, "", None
                )
        self.assertEqual(review["category"], "env_issue")
        self.assertEqual(review["failure_type"], "runtime_dependency")
        self.assertFalse(review["can_auto_repair"])
        self.assertIn("mime-db", review["reason"])

    def test_dependency_failure_cannot_generate_yaml_repair(self):
        with self.assertRaisesRegex(ValueError, "环境/配置问题"):
            repair_service.repair_job_and_create_next(
                {"stdout_tail": NODE_DEPENDENCY_ERROR, "stderr_tail": ""}
            )


if __name__ == "__main__":
    unittest.main()
