"""Validate and store load-test datasets outside public application roots."""

import csv
import hashlib
import io
import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import uuid

from .. import access
from ..models.load_testing import ApiLoadDataset
from ..models.project import ApiProject


MAX_DATASET_BYTES = 10 * 1024 * 1024
MAX_DATASET_ROWS = 100_000
MAX_DATASET_FIELDS = 200
USAGE_MODES = frozenset({"cycle", "fixed_per_vu", "exclusive_per_iteration"})
SECRET_FIELD_PARTS = (
    "password",
    "passwd",
    "token",
    "secret",
    "authorization",
    "cookie",
    "api_key",
    "apikey",
    "密码",
    "密钥",
    "令牌",
)
PERSONAL_FIELD_PARTS = ("phone", "mobile", "email", "mail", "手机号", "电话", "邮箱")


class LoadDatasetError(ValueError):
    """Raised when an uploaded dataset is invalid or unsafe."""


@dataclass(frozen=True)
class LoadDatasetImportResult:
    id: str
    name: str
    filename: str
    usage_mode: str
    row_count: int
    fields: tuple
    preview_rows: tuple
    content_hash: str
    sensitivity: str


class LoadDatasetService:
    def __init__(self, session_factory, *, data_root=None):
        self.session_factory = session_factory
        configured = data_root or os.getenv("API_LOAD_DATA_DIR", "/opt/midscene-api-load-data")
        self.data_root = Path(configured).expanduser()

    def import_bytes(self, project_id, name, filename, content, mode, actor_id):
        access.require_permission(actor_id, "api.loadtest.edit")
        project_id = self._text(project_id, "项目 ID", 100)
        name = self._text(name, "数据集名称", 200)
        filename = self._safe_filename(filename)
        if mode not in USAGE_MODES:
            raise LoadDatasetError("数据取用方式必须是循环共享、每个用户固定一行或每次迭代独占一行")
        if not isinstance(content, bytes):
            raise LoadDatasetError("数据文件内容必须是字节数据")
        if len(content) > MAX_DATASET_BYTES:
            raise LoadDatasetError(f"数据文件不能超过 {MAX_DATASET_BYTES // 1024 // 1024} MB")
        if not content:
            raise LoadDatasetError("数据文件不能为空")

        rows, fields = self._parse_file(filename, content)
        sensitivity = self._classify_fields(fields)
        canonical = json.dumps(
            rows, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        content_hash = hashlib.sha256(canonical).hexdigest()
        storage_path = None
        try:
            with self.session_factory.begin() as session:
                project = session.get(ApiProject, project_id)
                if project is None:
                    raise LoadDatasetError("项目不存在或已删除")
                access.require_resource(session, project, actor_id, "api.loadtest.edit")
                storage_path = self._write_private(canonical)
                audit = access.inherited_audit(session, actor_id, ApiProject, project_id)
                record = ApiLoadDataset(
                    project_id=project_id,
                    name=name,
                    filename=filename,
                    field_schema={
                        "fields": [
                            {"name": field, "types": self._field_types(rows, field)}
                            for field in fields
                        ]
                    },
                    row_count=len(rows),
                    storage_ref=str(storage_path),
                    content_hash=content_hash,
                    sensitivity=sensitivity,
                    usage_mode=mode,
                    **audit,
                )
                session.add(record)
                session.flush()
                result = LoadDatasetImportResult(
                    id=record.id,
                    name=record.name,
                    filename=record.filename,
                    usage_mode=record.usage_mode,
                    row_count=record.row_count,
                    fields=tuple(fields),
                    preview_rows=tuple(self._preview_row(row) for row in rows[:3]),
                    content_hash=content_hash,
                    sensitivity=sensitivity,
                )
        except Exception:
            if storage_path is not None:
                storage_path.unlink(missing_ok=True)
            raise
        return result

    @staticmethod
    def _text(value, field, maximum):
        value = value.strip() if isinstance(value, str) else ""
        if not value or len(value) > maximum:
            raise LoadDatasetError(f"{field}必须是 1 到 {maximum} 个字符")
        return value

    @classmethod
    def _safe_filename(cls, value):
        value = cls._text(value, "文件名", 255)
        if Path(value).name != value or "/" in value or "\\" in value:
            raise LoadDatasetError("文件名不能包含路径")
        suffix = Path(value).suffix.lower()
        if suffix not in {".csv", ".json"}:
            raise LoadDatasetError("数据文件只支持 CSV 或 JSON")
        return value

    @classmethod
    def _parse_file(cls, filename, content):
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise LoadDatasetError("数据文件必须使用 UTF-8 编码") from exc
        if "\x00" in text:
            raise LoadDatasetError("数据文件不能包含空字节")
        if filename.lower().endswith(".csv"):
            return cls._parse_csv(text)
        return cls._parse_json(text)

    @classmethod
    def _parse_csv(cls, text):
        try:
            table = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise LoadDatasetError(f"CSV 格式错误：{exc}") from exc
        if not table:
            raise LoadDatasetError("CSV 必须包含字段名和至少一行数据")
        fields = [item.strip() for item in table[0]]
        cls._validate_fields(fields)
        rows = []
        for line_number, values in enumerate(table[1:], start=2):
            if not values or (len(values) == 1 and not values[0]):
                continue
            if len(values) != len(fields):
                raise LoadDatasetError(
                    f"CSV 第 {line_number} 行列数为 {len(values)}，应为 {len(fields)}"
                )
            rows.append(dict(zip(fields, values)))
        cls._validate_row_count(rows)
        return rows, fields

    @classmethod
    def _parse_json(cls, text):
        def unique_object(pairs):
            keys = [key for key, _ in pairs]
            if len(keys) != len(set(keys)):
                raise LoadDatasetError("JSON 对象的字段名不能重复")
            return dict(pairs)

        try:
            value = json.loads(text, object_pairs_hook=unique_object)
        except json.JSONDecodeError as exc:
            raise LoadDatasetError(f"JSON 格式错误：第 {exc.lineno} 行第 {exc.colno} 列") from exc
        if not isinstance(value, list):
            raise LoadDatasetError("JSON 顶层必须是数组")
        cls._validate_row_count(value)
        if not all(isinstance(item, dict) for item in value):
            raise LoadDatasetError("JSON 数组中的每一项必须是对象")
        fields = list(value[0])
        cls._validate_fields(fields)
        expected = set(fields)
        rows = []
        for index, item in enumerate(value, start=1):
            if set(item) != expected:
                raise LoadDatasetError(f"JSON 第 {index} 项的字段必须一致")
            try:
                json.dumps(item, ensure_ascii=False, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise LoadDatasetError(f"JSON 第 {index} 项包含不支持的数据") from exc
            rows.append({field: item[field] for field in fields})
        return rows, fields

    @staticmethod
    def _validate_row_count(rows):
        if not rows:
            raise LoadDatasetError("数据文件至少需要一行数据")
        if len(rows) > MAX_DATASET_ROWS:
            raise LoadDatasetError(f"数据文件不能超过 {MAX_DATASET_ROWS} 行")

    @staticmethod
    def _validate_fields(fields):
        if not fields or len(fields) > MAX_DATASET_FIELDS:
            raise LoadDatasetError(f"字段数量必须在 1 到 {MAX_DATASET_FIELDS} 之间")
        if any(not isinstance(field, str) or not field or len(field) > 200 for field in fields):
            raise LoadDatasetError("字段名不能为空且不能超过 200 个字符")
        if len(fields) != len(set(fields)):
            raise LoadDatasetError("字段名不能重复")
        unsafe = [
            field
            for field in fields
            if any(part in field.lower() for part in SECRET_FIELD_PARTS)
        ]
        if unsafe:
            raise LoadDatasetError(
                f"普通数据集不能包含密码、Token 或密钥字段：{unsafe[0]}；请改用环境密钥"
            )

    @staticmethod
    def _classify_fields(fields):
        return (
            "personal"
            if any(
                part in field.lower()
                for field in fields
                for part in PERSONAL_FIELD_PARTS
            )
            else "normal"
        )

    @staticmethod
    def _field_types(rows, field):
        names = []
        for row in rows:
            value = row[field]
            name = (
                "null" if value is None else
                "boolean" if isinstance(value, bool) else
                "number" if isinstance(value, (int, float)) else
                "array" if isinstance(value, list) else
                "object" if isinstance(value, dict) else
                "string"
            )
            if name not in names:
                names.append(name)
        return names

    @staticmethod
    def _preview_row(row):
        result = {}
        for field, value in row.items():
            lowered = field.lower()
            if any(part in lowered for part in ("phone", "mobile", "手机号", "电话")):
                digits = str(value)
                value = digits[:3] + "****" + digits[-4:] if len(digits) >= 7 else "****"
            elif any(part in lowered for part in ("email", "mail", "邮箱")):
                text = str(value)
                local, separator, domain = text.partition("@")
                value = (local[:1] + "***@" + domain) if separator else "****"
            elif isinstance(value, str) and len(value) > 80:
                value = value[:40] + "…"
            result[field] = value
        return result

    def _write_private(self, content):
        if self.data_root.is_symlink():
            raise LoadDatasetError("数据目录不能是符号链接")
        self.data_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.data_root, 0o700)
        filename = uuid.uuid4().hex + ".json"
        path = self.data_root / filename
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            path.unlink(missing_ok=True)
            raise
        os.chmod(path, 0o600)
        return path
