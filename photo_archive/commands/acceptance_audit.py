from pathlib import Path
from typing import Dict, List, Optional, Tuple
import csv
import json
import logging
import os
import uuid
import yaml
from datetime import datetime

from ..models import (
    AcceptanceAuditConfig,
    AcceptanceAuditRecord,
    AcceptanceCheckResult,
    AcceptanceCheckStatus,
    AcceptanceCheckType,
    AcceptanceRule,
    AcceptanceConfigError,
    AcceptanceRuleConflictError,
    AcceptanceDirectoryNotFoundError,
    AcceptanceExportExistsError,
    AcceptanceWritePermissionError,
)
from ..storage import AcceptanceAuditStorage


logger = logging.getLogger(__name__)


def load_acceptance_config(config_path: str) -> AcceptanceAuditConfig:
    path = Path(config_path)
    if not path.exists():
        raise AcceptanceConfigError(f"验收配置文件不存在: {config_path}", missing_fields=["config_path"])

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    return parse_acceptance_config(data)


def parse_acceptance_config(data: Dict) -> AcceptanceAuditConfig:
    required_fields = ["client_name", "batch_name", "source_dir"]
    missing_fields = [f for f in required_fields if f not in data or data[f] is None or data[f] == ""]

    if missing_fields:
        raise AcceptanceConfigError(
            f"验收配置缺少必填字段: {', '.join(missing_fields)}",
            missing_fields=missing_fields
        )

    rules_data = data.get("rules", [])
    rules = []
    for rule_data in rules_data:
        if not isinstance(rule_data, dict):
            continue
        if "rule_id" not in rule_data or "rule_type" not in rule_data:
            continue
        rules.append(AcceptanceRule.from_dict(rule_data))

    _validate_rule_conflicts(rules)

    config = AcceptanceAuditConfig(
        client_name=data["client_name"],
        batch_name=data["batch_name"],
        source_dir=data["source_dir"],
        rules=rules,
        manifest_file=data.get("manifest_file"),
        expected_photo_count=data.get("expected_photo_count"),
        allowed_extensions=data.get("allowed_extensions", []),
        min_file_size=data.get("min_file_size"),
        max_file_size=data.get("max_file_size"),
        required_directories=data.get("required_directories", []),
        required_files=data.get("required_files", []),
    )

    return config


def _validate_rule_conflicts(rules: List[AcceptanceRule]) -> None:
    enabled_rules = [r for r in rules if r.enabled]
    rule_types = {}

    for rule in enabled_rules:
        rule_type = rule.rule_type.value
        if rule_type not in rule_types:
            rule_types[rule_type] = []
        rule_types[rule_type].append(rule.rule_id)

    conflicting_rules = []
    for rule_type, rule_ids in rule_types.items():
        if len(rule_ids) > 1:
            conflicting_rules.append(f"{rule_type}: {', '.join(rule_ids)}")

    if conflicting_rules:
        raise AcceptanceRuleConflictError(
            f"存在规则冲突，同一类型的规则只能启用一个: {'; '.join(conflicting_rules)}",
            conflicting_rules=conflicting_rules
        )


def run_acceptance_audit(
    config: AcceptanceAuditConfig,
    storage: AcceptanceAuditStorage,
    export_json: Optional[str] = None,
    export_csv: Optional[str] = None,
    overwrite: bool = False,
) -> Dict:
    source_dir = Path(config.source_dir)
    if not source_dir.exists():
        raise AcceptanceDirectoryNotFoundError(str(source_dir))

    if not source_dir.is_dir():
        raise AcceptanceDirectoryNotFoundError(f"路径不是目录: {source_dir}")

    audit_id = str(uuid.uuid4())[:8]
    record = AcceptanceAuditRecord(
        audit_id=audit_id,
        client_name=config.client_name,
        batch_name=config.batch_name,
        source_dir=str(source_dir),
        started_at=datetime.now(),
        config=config,
    )

    _log_entry(record, "AUDIT_START", {
        "audit_id": audit_id,
        "client_name": config.client_name,
        "batch_name": config.batch_name,
        "source_dir": str(source_dir),
    })

    storage.save(record)

    try:
        _check_export_paths(export_json, export_csv, overwrite)

        all_files = _collect_all_files(source_dir)

        for rule in config.rules:
            if not rule.enabled:
                continue

            _log_entry(record, "RULE_CHECK_START", {
                "rule_id": rule.rule_id,
                "rule_type": rule.rule_type.value,
                "description": rule.description,
            })

            result = _execute_rule_check(rule, config, source_dir, all_files)
            record.results.append(result)

            _log_entry(record, "RULE_CHECK_END", {
                "rule_id": rule.rule_id,
                "status": result.status.value,
                "message": result.message,
            })

        _apply_auto_checks(config, source_dir, all_files, record)

        record.status = AcceptanceCheckStatus.PASS
        fail_count = sum(1 for r in record.results if r.status == AcceptanceCheckStatus.FAIL)
        if fail_count > 0:
            record.status = AcceptanceCheckStatus.FAIL
        elif sum(1 for r in record.results if r.status == AcceptanceCheckStatus.WARNING) > 0:
            record.status = AcceptanceCheckStatus.WARNING

        record.completed_at = datetime.now()

        if export_json:
            json_path = _export_json(record, export_json, overwrite)
            record.exported_paths["json"] = json_path
            _log_entry(record, "EXPORT_JSON", {"path": json_path})

        if export_csv:
            csv_path = _export_csv(record, export_csv, overwrite)
            record.exported_paths["csv"] = csv_path
            _log_entry(record, "EXPORT_CSV", {"path": csv_path})

        _log_entry(record, "AUDIT_COMPLETE", {
            "status": record.status.value,
            "statistics": record.get_statistics(),
            "exported_paths": record.exported_paths,
        })

        storage.save(record)

        return _build_audit_result(record)

    except Exception as e:
        record.status = AcceptanceCheckStatus.FAIL
        record.error_message = str(e)
        record.completed_at = datetime.now()
        _log_entry(record, "AUDIT_FAILED", {"error": str(e)}, success=False)
        storage.save(record)
        raise


def _check_export_paths(export_json: Optional[str], export_csv: Optional[str], overwrite: bool) -> None:
    for path_str in [export_json, export_csv]:
        if not path_str:
            continue
        path = Path(path_str)
        if path.exists() and not overwrite:
            raise AcceptanceExportExistsError(str(path))
        parent_dir = path.parent
        if not parent_dir.exists():
            parent_dir.mkdir(parents=True, exist_ok=True)
        try:
            test_file = parent_dir / f".write_test_{uuid.uuid4().hex}"
            with open(test_file, "w", encoding="utf-8") as f:
                f.write("write_permission_test")
            test_file.unlink()
        except PermissionError:
            raise AcceptanceWritePermissionError(str(parent_dir), str(path))


def _collect_all_files(source_dir: Path) -> List[Path]:
    files = []
    for root, dirs, filenames in os.walk(source_dir):
        for filename in filenames:
            files.append(Path(root) / filename)
    return files


def _execute_rule_check(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
) -> AcceptanceCheckResult:
    check_type = rule.rule_type
    params = rule.parameters

    if check_type == AcceptanceCheckType.DIRECTORY_STRUCTURE:
        return _check_directory_structure(rule, config, source_dir, params)
    elif check_type == AcceptanceCheckType.REQUIRED_FILES:
        return _check_required_files(rule, config, source_dir, params)
    elif check_type == AcceptanceCheckType.PHOTO_COUNT:
        return _check_photo_count(rule, config, source_dir, all_files, params)
    elif check_type == AcceptanceCheckType.FILE_SIZE:
        return _check_file_sizes(rule, config, source_dir, all_files, params)
    elif check_type == AcceptanceCheckType.EXTENSION:
        return _check_extensions(rule, config, source_dir, all_files, params)
    elif check_type == AcceptanceCheckType.DUPLICATE_FILENAME:
        return _check_duplicate_filenames(rule, config, source_dir, all_files, params)
    elif check_type == AcceptanceCheckType.MISSING_MANIFEST:
        return _check_missing_manifest(rule, config, source_dir, all_files, params)
    else:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=check_type,
            status=AcceptanceCheckStatus.WARNING,
            message=f"未知的检查类型: {check_type}",
        )


def _apply_auto_checks(
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    record: AcceptanceAuditRecord,
) -> None:
    existing_rule_types = {r.rule_type for r in record.results}

    if config.required_directories and AcceptanceCheckType.DIRECTORY_STRUCTURE not in existing_rule_types:
        result = _check_directory_structure(
            AcceptanceRule(
                rule_id="auto_directory_structure",
                rule_type=AcceptanceCheckType.DIRECTORY_STRUCTURE,
                description="自动检查必备目录结构",
                parameters={"directories": config.required_directories},
            ),
            config,
            source_dir,
            {"directories": config.required_directories},
        )
        record.results.append(result)

    if config.required_files and AcceptanceCheckType.REQUIRED_FILES not in existing_rule_types:
        result = _check_required_files(
            AcceptanceRule(
                rule_id="auto_required_files",
                rule_type=AcceptanceCheckType.REQUIRED_FILES,
                description="自动检查必备文件",
                parameters={"files": config.required_files},
            ),
            config,
            source_dir,
            {"files": config.required_files},
        )
        record.results.append(result)

    if config.expected_photo_count and AcceptanceCheckType.PHOTO_COUNT not in existing_rule_types:
        result = _check_photo_count(
            AcceptanceRule(
                rule_id="auto_photo_count",
                rule_type=AcceptanceCheckType.PHOTO_COUNT,
                description="自动检查照片数量",
                parameters={"expected_count": config.expected_photo_count},
            ),
            config,
            source_dir,
            all_files,
            {"expected_count": config.expected_photo_count},
        )
        record.results.append(result)

    if (config.min_file_size or config.max_file_size) and AcceptanceCheckType.FILE_SIZE not in existing_rule_types:
        result = _check_file_sizes(
            AcceptanceRule(
                rule_id="auto_file_size",
                rule_type=AcceptanceCheckType.FILE_SIZE,
                description="自动检查文件大小",
                parameters={
                    "min_size": config.min_file_size,
                    "max_size": config.max_file_size,
                },
            ),
            config,
            source_dir,
            all_files,
            {
                "min_size": config.min_file_size,
                "max_size": config.max_file_size,
            },
        )
        record.results.append(result)

    if config.allowed_extensions and AcceptanceCheckType.EXTENSION not in existing_rule_types:
        result = _check_extensions(
            AcceptanceRule(
                rule_id="auto_extension",
                rule_type=AcceptanceCheckType.EXTENSION,
                description="自动检查文件扩展名",
                parameters={"allowed_extensions": config.allowed_extensions},
            ),
            config,
            source_dir,
            all_files,
            {"allowed_extensions": config.allowed_extensions},
        )
        record.results.append(result)

    if AcceptanceCheckType.DUPLICATE_FILENAME not in existing_rule_types:
        result = _check_duplicate_filenames(
            AcceptanceRule(
                rule_id="auto_duplicate_filename",
                rule_type=AcceptanceCheckType.DUPLICATE_FILENAME,
                description="自动检查重复文件名",
                parameters={},
            ),
            config,
            source_dir,
            all_files,
            {},
        )
        record.results.append(result)

    if config.manifest_file and AcceptanceCheckType.MISSING_MANIFEST not in existing_rule_types:
        result = _check_missing_manifest(
            AcceptanceRule(
                rule_id="auto_missing_manifest",
                rule_type=AcceptanceCheckType.MISSING_MANIFEST,
                description="自动检查清单缺失",
                parameters={"manifest_file": config.manifest_file},
            ),
            config,
            source_dir,
            all_files,
            {"manifest_file": config.manifest_file},
        )
        record.results.append(result)


def _check_directory_structure(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    params: Dict,
) -> AcceptanceCheckResult:
    required_dirs = params.get("directories", config.required_directories)
    missing_dirs = []
    present_dirs = []

    for dir_name in required_dirs:
        dir_path = source_dir / dir_name
        if dir_path.exists() and dir_path.is_dir():
            present_dirs.append(dir_name)
        else:
            missing_dirs.append(dir_name)

    status = AcceptanceCheckStatus.PASS if not missing_dirs else AcceptanceCheckStatus.FAIL
    message = f"目录结构检查通过，共 {len(present_dirs)} 个必备目录存在" if not missing_dirs else \
              f"缺少 {len(missing_dirs)} 个必备目录: {', '.join(missing_dirs)}"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.DIRECTORY_STRUCTURE,
        status=status,
        message=message,
        details=[
            {"directory": d, "exists": True} for d in present_dirs
        ] + [
            {"directory": d, "exists": False} for d in missing_dirs
        ],
    )


def _check_required_files(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    params: Dict,
) -> AcceptanceCheckResult:
    required_files = params.get("files", config.required_files)
    missing_files = []
    present_files = []

    for file_name in required_files:
        file_path = source_dir / file_name
        if file_path.exists() and file_path.is_file():
            present_files.append({
                "file": file_name,
                "size": file_path.stat().st_size,
            })
        else:
            missing_files.append(file_name)

    status = AcceptanceCheckStatus.PASS if not missing_files else AcceptanceCheckStatus.FAIL
    message = f"必备文件检查通过，共 {len(present_files)} 个文件存在" if not missing_files else \
              f"缺少 {len(missing_files)} 个必备文件: {', '.join(missing_files)}"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.REQUIRED_FILES,
        status=status,
        message=message,
        details=present_files + [{"file": f, "exists": False} for f in missing_files],
    )


STANDARD_PHOTO_EXTENSIONS = [
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".raw", ".cr2", ".nef", ".arw",
    ".heic", ".heif", ".webp", ".dng",
]

STANDARD_MEDIA_EXTENSIONS = STANDARD_PHOTO_EXTENSIONS + [
    ".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".m4v",
]


def _check_photo_count(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    params: Dict,
) -> AcceptanceCheckResult:
    expected_count = params.get("expected_count", config.expected_photo_count)
    photo_extensions_param = params.get("photo_extensions")

    photo_extensions = photo_extensions_param if photo_extensions_param else STANDARD_PHOTO_EXTENSIONS
    photo_extensions = [ext.lower() for ext in photo_extensions]

    photo_files = [f for f in all_files if f.suffix.lower() in photo_extensions]
    actual_count = len(photo_files)

    details = [
        {"actual_count": actual_count, "expected_count": expected_count, "extensions": photo_extensions},
    ]

    if expected_count is None:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.PHOTO_COUNT,
            status=AcceptanceCheckStatus.PASS,
            message=f"照片数量统计: {actual_count} 张（未设置期望值）",
            details=details,
        )

    tolerance = params.get("tolerance", 0)
    min_count = max(0, expected_count - tolerance)
    max_count = expected_count + tolerance

    if min_count <= actual_count <= max_count:
        status = AcceptanceCheckStatus.PASS
        message = f"照片数量检查通过: {actual_count} 张（期望 {expected_count}，容差 ±{tolerance}）"
    elif actual_count < min_count:
        status = AcceptanceCheckStatus.FAIL
        message = f"照片数量不足: {actual_count} 张，期望至少 {min_count} 张，缺少 {min_count - actual_count} 张"
    else:
        status = AcceptanceCheckStatus.WARNING
        message = f"照片数量超出期望: {actual_count} 张，期望最多 {max_count} 张，多出 {actual_count - max_count} 张"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.PHOTO_COUNT,
        status=status,
        message=message,
        details=details,
    )


def _check_file_sizes(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    params: Dict,
) -> AcceptanceCheckResult:
    min_size = params.get("min_size", config.min_file_size)
    max_size = params.get("max_size", config.max_file_size)
    target_extensions_param = params.get("target_extensions")

    if min_size is None and max_size is None:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.FILE_SIZE,
            status=AcceptanceCheckStatus.PASS,
            message="未设置文件大小限制，跳过检查",
            details=[{"min_size": None, "max_size": None}],
        )

    target_extensions = [ext.lower() for ext in target_extensions_param] if target_extensions_param else STANDARD_MEDIA_EXTENSIONS
    target_files = [f for f in all_files if f.suffix.lower() in target_extensions]

    too_small = []
    too_large = []
    ok_files = []

    for f in target_files:
        try:
            size = f.stat().st_size
            file_info = {
                "file": str(f.relative_to(source_dir)),
                "size": size,
            }
            if min_size is not None and size < min_size:
                too_small.append(file_info)
            elif max_size is not None and size > max_size:
                too_large.append(file_info)
            else:
                ok_files.append(file_info)
        except OSError:
            pass

    details = [
        {"min_size": min_size, "max_size": max_size, "checked_files": len(target_files)},
        {"too_small_count": len(too_small), "too_large_count": len(too_large), "ok_count": len(ok_files)},
    ]

    if too_small or too_large:
        status = AcceptanceCheckStatus.FAIL
        parts = []
        if too_small:
            parts.append(f"{len(too_small)} 个文件过小")
        if too_large:
            parts.append(f"{len(too_large)} 个文件过大")
        message = f"文件大小检查失败: {', '.join(parts)}"
    else:
        status = AcceptanceCheckStatus.PASS
        message = f"文件大小检查通过，共 {len(ok_files)} 个文件符合要求"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.FILE_SIZE,
        status=status,
        message=message,
        details=details + too_small + too_large,
    )


def _check_extensions(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    params: Dict,
) -> AcceptanceCheckResult:
    allowed_extensions = params.get("allowed_extensions", config.allowed_extensions)
    if not allowed_extensions:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.EXTENSION,
            status=AcceptanceCheckStatus.PASS,
            message="未设置扩展名限制，跳过检查",
            details=[{"allowed_extensions": []}],
        )

    allowed = [ext.lower() if ext.startswith(".") else f".{ext.lower()}" for ext in allowed_extensions]

    invalid_files = []
    valid_files = []
    extension_counts = {}

    for f in all_files:
        ext = f.suffix.lower()
        if ext not in extension_counts:
            extension_counts[ext] = 0
        extension_counts[ext] += 1

        if ext not in allowed:
            invalid_files.append({
                "file": str(f.relative_to(source_dir)),
                "extension": ext,
            })
        else:
            valid_files.append({
                "file": str(f.relative_to(source_dir)),
                "extension": ext,
            })

    details = [
        {"allowed_extensions": allowed},
        {"extension_counts": extension_counts},
        {"invalid_count": len(invalid_files), "valid_count": len(valid_files)},
    ]

    if invalid_files:
        status = AcceptanceCheckStatus.FAIL
        message = f"扩展名检查失败: {len(invalid_files)} 个文件使用了不允许的扩展名"
    else:
        status = AcceptanceCheckStatus.PASS
        message = f"扩展名检查通过，共 {len(valid_files)} 个文件使用合法扩展名"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.EXTENSION,
        status=status,
        message=message,
        details=details + invalid_files[:20],
    )


def _check_duplicate_filenames(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    params: Dict,
) -> AcceptanceCheckResult:
    name_map: Dict[str, List[str]] = {}

    for f in all_files:
        name = f.name
        if name not in name_map:
            name_map[name] = []
        name_map[name].append(str(f.relative_to(source_dir)))

    duplicates = {name: paths for name, paths in name_map.items() if len(paths) > 1}

    details = [
        {"total_files": len(all_files), "unique_names": len(name_map), "duplicate_groups": len(duplicates)},
    ]

    if duplicates:
        status = AcceptanceCheckStatus.FAIL
        message = f"发现 {len(duplicates)} 组重复文件名"
        for name, paths in sorted(duplicates.items()):
            details.append({
                "filename": name,
                "count": len(paths),
                "locations": paths,
            })
    else:
        status = AcceptanceCheckStatus.PASS
        message = f"文件名检查通过，{len(all_files)} 个文件无重名"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.DUPLICATE_FILENAME,
        status=status,
        message=message,
        details=details,
    )


def _check_missing_manifest(
    rule: AcceptanceRule,
    config: AcceptanceAuditConfig,
    source_dir: Path,
    all_files: List[Path],
    params: Dict,
) -> AcceptanceCheckResult:
    manifest_file = params.get("manifest_file", config.manifest_file)
    if not manifest_file:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.MISSING_MANIFEST,
            status=AcceptanceCheckStatus.PASS,
            message="未设置清单文件，跳过检查",
            details=[{"manifest_file": None}],
        )

    manifest_path = source_dir / manifest_file
    if not manifest_path.exists():
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.MISSING_MANIFEST,
            status=AcceptanceCheckStatus.FAIL,
            message=f"清单文件不存在: {manifest_file}",
            details=[{"manifest_file": manifest_file, "exists": False}],
        )

    try:
        import csv
        with open(manifest_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            expected_files = []
            for row in reader:
                if "target_name" in row:
                    expected_files.append(row["target_name"])
                elif "filename" in row:
                    expected_files.append(row["filename"])
                elif "file_name" in row:
                    expected_files.append(row["file_name"])
                elif len(row) > 0:
                    expected_files.append(list(row.values())[0])
    except Exception as e:
        return AcceptanceCheckResult(
            rule_id=rule.rule_id,
            rule_type=AcceptanceCheckType.MISSING_MANIFEST,
            status=AcceptanceCheckStatus.FAIL,
            message=f"读取清单文件失败: {e}",
            details=[{"manifest_file": manifest_file, "error": str(e)}],
        )

    actual_names = {f.name for f in all_files}
    missing_files = []
    found_files = []

    for expected in expected_files:
        if expected in actual_names:
            found_files.append(expected)
        else:
            missing_files.append(expected)

    details = [
        {"manifest_file": manifest_file, "expected_count": len(expected_files)},
        {"found_count": len(found_files), "missing_count": len(missing_files)},
    ]

    if missing_files:
        status = AcceptanceCheckStatus.FAIL
        message = f"清单检查失败: 缺少 {len(missing_files)} 个文件"
        details.append({"missing_files": missing_files})
    else:
        status = AcceptanceCheckStatus.PASS
        message = f"清单检查通过: {len(found_files)} 个文件全部存在"

    return AcceptanceCheckResult(
        rule_id=rule.rule_id,
        rule_type=AcceptanceCheckType.MISSING_MANIFEST,
        status=status,
        message=message,
        details=details,
    )


def _export_json(record: AcceptanceAuditRecord, export_path: str, overwrite: bool) -> str:
    path = Path(export_path)
    if path.exists() and not overwrite:
        raise AcceptanceExportExistsError(str(path))

    data = {
        "audit_id": record.audit_id,
        "client_name": record.client_name,
        "batch_name": record.batch_name,
        "source_dir": record.source_dir,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "status": record.status.value,
        "statistics": record.get_statistics(),
        "config": record.config.to_dict() if record.config else None,
        "results": [r.to_dict() for r in record.results],
        "exported_at": datetime.now().isoformat(),
    }

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    return str(path)


def _export_csv(record: AcceptanceAuditRecord, export_path: str, overwrite: bool) -> str:
    path = Path(export_path)
    if path.exists() and not overwrite:
        raise AcceptanceExportExistsError(str(path))

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)

        writer.writerow(["=== 验收审计概览 ==="])
        writer.writerow(["审计ID", record.audit_id])
        writer.writerow(["客户", record.client_name])
        writer.writerow(["批次", record.batch_name])
        writer.writerow(["源目录", record.source_dir])
        writer.writerow(["开始时间", record.started_at.isoformat()])
        writer.writerow(["完成时间", record.completed_at.isoformat() if record.completed_at else ""])
        writer.writerow(["状态", record.status.value])

        stats = record.get_statistics()
        writer.writerow(["总规则数", stats["total_rules"]])
        writer.writerow(["通过", stats["passed"]])
        writer.writerow(["失败", stats["failed"]])
        writer.writerow(["警告", stats["warnings"]])
        writer.writerow(["总体结果", stats["overall"]])
        writer.writerow([])

        writer.writerow(["=== 检查结果明细 ==="])
        writer.writerow(["规则ID", "检查类型", "状态", "消息"])
        for result in record.results:
            writer.writerow([
                result.rule_id,
                result.rule_type.value,
                result.status.value,
                result.message,
            ])
        writer.writerow([])

        for result in record.results:
            if result.details:
                writer.writerow([f"=== {result.rule_id} 详细信息 ==="])
                writer.writerow([f"类型: {result.rule_type.value}"])
                writer.writerow([f"状态: {result.status.value}"])
                writer.writerow([f"消息: {result.message}"])
                writer.writerow([])
                writer.writerow(["详细数据"])
                for detail in result.details:
                    writer.writerow([json.dumps(detail, ensure_ascii=False)])
                writer.writerow([])

        if record.exported_paths:
            writer.writerow(["=== 导出文件 ==="])
            for fmt, p in record.exported_paths.items():
                writer.writerow([fmt, p])
            writer.writerow([])

        if record.log_entries:
            writer.writerow(["=== 审计日志 ==="])
            writer.writerow(["时间", "操作", "成功", "详情"])
            for entry in record.log_entries:
                writer.writerow([
                    entry.get("timestamp", ""),
                    entry.get("operation", ""),
                    entry.get("success", True),
                    json.dumps(entry.get("details", {}), ensure_ascii=False),
                ])

    return str(path)


def generate_text_summary(record: AcceptanceAuditRecord) -> str:
    stats = record.get_statistics()
    lines = []

    lines.append("=" * 60)
    lines.append("  交付验收审计报告")
    lines.append("=" * 60)
    lines.append(f"审计ID:    {record.audit_id}")
    lines.append(f"客户:      {record.client_name}")
    lines.append(f"批次:      {record.batch_name}")
    lines.append(f"源目录:    {record.source_dir}")
    lines.append(f"开始时间:  {record.started_at.strftime('%Y-%m-%d %H:%M:%S')}")
    if record.completed_at:
        lines.append(f"完成时间:  {record.completed_at.strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"状态:      {'通过' if record.status == AcceptanceCheckStatus.PASS else '失败' if record.status == AcceptanceCheckStatus.FAIL else '警告'}")
    lines.append("")

    lines.append("-" * 60)
    lines.append("  统计摘要")
    lines.append("-" * 60)
    lines.append(f"总检查项:  {stats['total_rules']}")
    lines.append(f"通过:      {stats['passed']}")
    lines.append(f"失败:      {stats['failed']}")
    lines.append(f"警告:      {stats['warnings']}")
    lines.append(f"总体结果:  {'✓ 通过' if stats['overall'] == 'PASS' else '✗ 失败'}")
    lines.append("")

    lines.append("-" * 60)
    lines.append("  检查结果明细")
    lines.append("-" * 60)

    for i, result in enumerate(record.results, 1):
        status_icon = {
            AcceptanceCheckStatus.PASS: "✓",
            AcceptanceCheckStatus.FAIL: "✗",
            AcceptanceCheckStatus.WARNING: "!",
        }.get(result.status, "?")

        type_name = {
            AcceptanceCheckType.DIRECTORY_STRUCTURE: "目录结构",
            AcceptanceCheckType.REQUIRED_FILES: "必备文件",
            AcceptanceCheckType.PHOTO_COUNT: "照片数量",
            AcceptanceCheckType.FILE_SIZE: "文件大小",
            AcceptanceCheckType.EXTENSION: "扩展名",
            AcceptanceCheckType.DUPLICATE_FILENAME: "重复文件名",
            AcceptanceCheckType.MISSING_MANIFEST: "清单缺失",
        }.get(result.rule_type, result.rule_type.value)

        lines.append(f"{status_icon} [{i}] {type_name} ({result.rule_id})")
        lines.append(f"    {result.message}")
        lines.append("")

    if record.exported_paths:
        lines.append("-" * 60)
        lines.append("  导出文件")
        lines.append("-" * 60)
        for fmt, p in record.exported_paths.items():
            lines.append(f"  {fmt.upper()}: {p}")
        lines.append("")

    lines.append("=" * 60)
    lines.append(f"报告生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("=" * 60)

    return "\n".join(lines)


def _build_audit_result(record: AcceptanceAuditRecord) -> Dict:
    return {
        "audit_id": record.audit_id,
        "client_name": record.client_name,
        "batch_name": record.batch_name,
        "source_dir": record.source_dir,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "status": record.status.value,
        "statistics": record.get_statistics(),
        "results": [r.to_dict() for r in record.results],
        "exported_paths": record.exported_paths,
        "text_summary": generate_text_summary(record),
    }


def _log_entry(
    record: AcceptanceAuditRecord,
    operation: str,
    details: Dict,
    success: bool = True,
) -> None:
    entry = {
        "timestamp": datetime.now().isoformat(),
        "operation": operation,
        "success": success,
        "details": details,
    }
    record.log_entries.append(entry)
    record.updated_at = datetime.now()

    level = logging.INFO if success else logging.ERROR
    logger.log(level, f"[{operation}] {json.dumps(details, ensure_ascii=False)}")


def list_audit_history(
    storage: AcceptanceAuditStorage,
    batch_name: Optional[str] = None,
    client_name: Optional[str] = None,
    limit: int = 10,
) -> Dict:
    if batch_name:
        records = storage.find_audits_by_batch(batch_name)
    elif client_name:
        records = storage.find_audits_by_client(client_name)
    else:
        records = storage.find_recent_audits(limit)

    return {
        "count": len(records),
        "audits": [
            {
                "audit_id": r.audit_id,
                "client_name": r.client_name,
                "batch_name": r.batch_name,
                "source_dir": r.source_dir,
                "started_at": r.started_at.isoformat(),
                "completed_at": r.completed_at.isoformat() if r.completed_at else None,
                "status": r.status.value,
                "statistics": r.get_statistics(),
                "exported_paths": r.exported_paths,
            }
            for r in records
        ],
    }


def get_audit_detail(
    storage: AcceptanceAuditStorage,
    audit_id: str,
) -> Dict:
    record = storage.load(audit_id)
    if not record:
        raise ValueError(f"找不到审计记录: {audit_id}")

    return {
        "audit_id": record.audit_id,
        "client_name": record.client_name,
        "batch_name": record.batch_name,
        "source_dir": record.source_dir,
        "started_at": record.started_at.isoformat(),
        "completed_at": record.completed_at.isoformat() if record.completed_at else None,
        "status": record.status.value,
        "statistics": record.get_statistics(),
        "config": record.config.to_dict() if record.config else None,
        "results": [r.to_dict() for r in record.results],
        "exported_paths": record.exported_paths,
        "log_entries": record.log_entries,
        "text_summary": generate_text_summary(record),
    }


def reexport_audit_result(
    storage: AcceptanceAuditStorage,
    audit_id: str,
    export_json: Optional[str] = None,
    export_csv: Optional[str] = None,
    overwrite: bool = False,
) -> Dict:
    record = storage.load(audit_id)
    if not record:
        raise ValueError(f"找不到审计记录: {audit_id}")

    _check_export_paths(export_json, export_csv, overwrite)

    if export_json:
        json_path = _export_json(record, export_json, overwrite)
        record.exported_paths["json"] = json_path
        _log_entry(record, "REEXPORT_JSON", {"path": json_path})

    if export_csv:
        csv_path = _export_csv(record, export_csv, overwrite)
        record.exported_paths["csv"] = csv_path
        _log_entry(record, "REEXPORT_CSV", {"path": csv_path})

    storage.save(record)

    return {
        "audit_id": record.audit_id,
        "exported_paths": record.exported_paths,
        "text_summary": generate_text_summary(record),
    }
