from pathlib import Path
from typing import Dict, List
import csv
import json

from ..config import Config
from ..models import BatchHistory, FileStatus
from ..storage import BatchStorage
from .utils import get_or_create_batch


def generate_report(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    output_path: str = None,
    format: str = "json",
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    report_data = _build_report_data(batch, config)

    if output_path:
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        if format.lower() == "csv":
            _write_csv_report(path, report_data)
        else:
            _write_json_report(path, report_data)

    return report_data


def _build_report_data(batch: BatchHistory, config: Config) -> Dict:
    file_mappings: List[Dict] = []
    missing_files: List[Dict] = []
    duplicate_files: List[Dict] = []
    hash_changes: List[Dict] = []
    applied_corrections: List[Dict] = []
    import_records: List[Dict] = []
    conflicts: List[Dict] = []
    unresolved_conflicts: List[Dict] = []

    for target_name, item in batch.delivery_list.items():
        entry = {
            "target_name": target_name,
            "original_path": item.matched_source or "",
            "status": item.status.value,
            "expected_hash": item.expected_hash or "",
            "actual_hash": item.actual_hash or "",
            "expected_camera": item.expected_camera or "",
            "sequence": item.sequence or "",
        }

        if item.status == FileStatus.MISSING:
            missing_files.append(entry)
        elif item.status == FileStatus.DUPLICATE:
            duplicate_files.append(entry)
        elif item.status == FileStatus.HASH_MISMATCH:
            hash_changes.append(entry)

        file_mappings.append(entry)

    for correction in batch.corrections:
        if correction.applied_at:
            applied_corrections.append({
                "id": correction.id,
                "type": correction.type.value,
                "source": correction.source,
                "target": correction.target,
                "reason": correction.reason,
                "applied_at": correction.applied_at.isoformat() if correction.applied_at else "",
                "rolled_back": correction.rolled_back,
            })

    undone_corrections = [c for c in applied_corrections if c["rolled_back"]]
    active_corrections = [c for c in applied_corrections if not c["rolled_back"]]

    for record in batch.import_records:
        import_records.append({
            "import_id": record.import_id,
            "source_file": record.source_file,
            "imported_at": record.imported_at.isoformat(),
            "imported_count": record.imported_count,
            "batch_name": record.batch_name,
        })

    for conflict in batch.conflicts:
        conflict_dict = {
            "conflict_id": conflict.conflict_id,
            "conflict_type": conflict.conflict_type.value,
            "target_name": conflict.target_name,
            "source_file": conflict.source_file,
            "batch_name": conflict.batch_name,
            "message": conflict.message,
            "details": conflict.details,
            "resolved": conflict.resolved,
            "resolved_at": conflict.resolved_at.isoformat() if conflict.resolved_at else "",
        }
        conflicts.append(conflict_dict)
        if not conflict.resolved:
            unresolved_conflicts.append(conflict_dict)

    summary = {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "normalized_name": batch.normalized_name,
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
        "merge_status": batch.merge_status.value,
        "total_delivery_items": len(batch.delivery_list),
        "total_scanned_files": len(batch.scanned_files),
        "ok_count": sum(1 for i in batch.delivery_list.values() if i.status == FileStatus.OK),
        "missing_count": len(missing_files),
        "duplicate_count": len(duplicate_files),
        "hash_mismatch_count": len(hash_changes),
        "total_conflicts": len(conflicts),
        "unresolved_conflicts_count": len(unresolved_conflicts),
        "total_imports": len(import_records),
        "total_applied_corrections": len(applied_corrections),
        "active_corrections_count": len(active_corrections),
        "undone_corrections_count": len(undone_corrections),
        "scan_source_dir": batch.scan_source_dir or "",
        "archive_dir": str(config.archive_dir / batch.name),
        "hash_strategy": config.hash_strategy,
    }

    return {
        "summary": summary,
        "file_mappings": file_mappings,
        "missing_files": missing_files,
        "duplicate_files": duplicate_files,
        "hash_changes": hash_changes,
        "applied_corrections": active_corrections,
        "undone_corrections": undone_corrections,
        "import_records": import_records,
        "conflicts": conflicts,
        "unresolved_conflicts": unresolved_conflicts,
    }


def _write_json_report(path: Path, data: Dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _write_csv_report(path: Path, data: Dict) -> None:
    summary = data["summary"]

    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["=== 批次概览 ==="])
        for key, value in summary.items():
            writer.writerow([key, value])
        writer.writerow([])

        if data.get("import_records"):
            writer.writerow(["=== 导入记录 ==="])
            writer.writerow(["导入ID", "源文件", "导入时间", "导入数量", "批次名"])
            for item in data["import_records"]:
                writer.writerow([
                    item["import_id"],
                    item["source_file"],
                    item["imported_at"],
                    item["imported_count"],
                    item["batch_name"],
                ])
            writer.writerow([])

        if data.get("conflicts"):
            writer.writerow(["=== 冲突列表 ==="])
            writer.writerow(["冲突ID", "冲突类型", "目标文件名", "源文件", "批次名", "是否已解决", "消息"])
            for item in data["conflicts"]:
                writer.writerow([
                    item["conflict_id"],
                    item["conflict_type"],
                    item.get("target_name", ""),
                    item.get("source_file", ""),
                    item.get("batch_name", ""),
                    item["resolved"],
                    item["message"],
                ])
            writer.writerow([])

        writer.writerow(["=== 文件映射 ==="])
        writer.writerow(["目标文件名", "原路径", "状态", "期望哈希", "实际哈希", "期望机位", "序号"])
        for item in data["file_mappings"]:
            writer.writerow([
                item["target_name"],
                item["original_path"],
                item["status"],
                item["expected_hash"],
                item["actual_hash"],
                item["expected_camera"],
                item["sequence"],
            ])
        writer.writerow([])

        if data["missing_files"]:
            writer.writerow(["=== 缺片 ==="])
            writer.writerow(["目标文件名", "状态"])
            for item in data["missing_files"]:
                writer.writerow([item["target_name"], item["status"]])
            writer.writerow([])

        if data["duplicate_files"]:
            writer.writerow(["=== 重复文件 ==="])
            writer.writerow(["目标文件名", "原路径", "状态"])
            for item in data["duplicate_files"]:
                writer.writerow([item["target_name"], item["original_path"], item["status"]])
            writer.writerow([])

        if data["hash_changes"]:
            writer.writerow(["=== 哈希变化 ==="])
            writer.writerow(["目标文件名", "原路径", "期望哈希", "实际哈希"])
            for item in data["hash_changes"]:
                writer.writerow([
                    item["target_name"],
                    item["original_path"],
                    item["expected_hash"],
                    item["actual_hash"],
                ])
            writer.writerow([])

        if data["applied_corrections"]:
            writer.writerow(["=== 实际执行的修正 ==="])
            writer.writerow(["ID", "类型", "源", "目标", "原因", "执行时间"])
            for item in data["applied_corrections"]:
                writer.writerow([
                    item["id"],
                    item["type"],
                    item["source"],
                    item["target"],
                    item["reason"],
                    item["applied_at"],
                ])
            writer.writerow([])

        if data.get("undone_corrections"):
            writer.writerow(["=== 已撤销的修正 ==="])
            writer.writerow(["ID", "类型", "源", "目标", "原因", "执行时间"])
            for item in data["undone_corrections"]:
                writer.writerow([
                    item["id"],
                    item["type"],
                    item["source"],
                    item["target"],
                    item["reason"],
                    item["applied_at"],
                ])
