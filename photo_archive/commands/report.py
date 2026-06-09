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

    summary = {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "created_at": batch.created_at.isoformat(),
        "updated_at": batch.updated_at.isoformat(),
        "total_delivery_items": len(batch.delivery_list),
        "total_scanned_files": len(batch.scanned_files),
        "ok_count": sum(1 for i in batch.delivery_list.values() if i.status == FileStatus.OK),
        "missing_count": len(missing_files),
        "duplicate_count": len(duplicate_files),
        "hash_mismatch_count": len(hash_changes),
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
