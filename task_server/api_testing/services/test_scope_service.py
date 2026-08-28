"""Active application and business-line checks for new API test operations."""

from ...services.business_line_service import (
    business_line_id,
    resolve_test_application,
)


class InactiveTestScopeError(ValueError):
    pass


def ensure_active_case_version_scopes(versions) -> None:
    for version in versions:
        template = dict(getattr(version, "request_template", None) or {})
        package = str(template.get("app_package") or "").strip()
        application = resolve_test_application(
            package,
            template.get("app_name"),
            template.get("business"),
            include_disabled=True,
        )
        if not application:
            raise InactiveTestScopeError("所选目标的应用未配置或已移除，请重新选择")
        if not application.get("enabled") or application.get("historical_only"):
            raise InactiveTestScopeError(
                f"所选目标的应用“{application['name']}”已停用，请重新选择"
            )
        try:
            business_line_id(
                template.get("business"),
                app_package=application["package"],
                require_active=True,
            )
        except ValueError as exc:
            raise InactiveTestScopeError(f"所选目标的业务不可用：{exc}") from exc
