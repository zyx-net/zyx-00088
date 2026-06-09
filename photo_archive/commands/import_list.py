from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import csv
import uuid

from ..config import Config
from ..models import (
    BatchHistory,
    Conflict,
    ConflictType,
    DeliveryItem,
    FileStatus,
    ImportRecord,
    MergeStatus,
)
from ..storage import BatchStorage
from .utils import get_or_create_batch


def _parse_manifest(path: Path) -> List[DeliveryItem]:
    items: List[DeliveryItem] = []
    has_corrupted = False

    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                target_name = row.get("target_name") or row.get("filename") or row.get("file")
                if not target_name:
                    has_corrupted = True
                    continue

                item = DeliveryItem(
                    target_name=target_name.strip(),
                    expected_hash=row.get("expected_hash") or row.get("hash") or None,
                    expected_camera=row.get("camera") or row.get("expected_camera") or None,
                    expected_size=int(row["size"]) if row.get("size") else None,
                    sequence=int(row["sequence"]) if row.get("sequence") else None,
                )
                items.append(item)
    except Exception as e:
        raise ValueError(f"清单文件格式损坏: {e}")

    if has_corrupted:
        raise ValueError("清单文件包含损坏的条目")

    duplicate_names = set()
    seen_names = set()
    for item in items:
        if item.target_name in seen_names:
            duplicate_names.add(item.target_name)
        seen_names.add(item.target_name)

    if duplicate_names:
        raise ValueError(f"清单中存在重复文件名: {', '.join(sorted(duplicate_names))}")

    return items


def _detect_target_name_conflicts(
    new_items: List[DeliveryItem],
    existing_items: Dict[str, DeliveryItem],
    batch_name: str,
) -> List[Conflict]:
    conflicts: List[Conflict] = []

    for new_item in new_items:
        target_name = new_item.target_name
        if target_name in existing_items:
            existing = existing_items[target_name]

            hash_diff = (
                new_item.expected_hash
                and existing.expected_hash
                and new_item.expected_hash != existing.expected_hash
            )
            size_diff = (
                new_item.expected_size
                and existing.expected_size
                and new_item.expected_size != existing.expected_size
            )
            camera_diff = (
                new_item.expected_camera
                and existing.expected_camera
                and new_item.expected_camera != existing.expected_camera
            )

            if hash_diff or size_diff or camera_diff:
                details = {
                    "new_expected_hash": new_item.expected_hash,
                    "existing_expected_hash": existing.expected_hash,
                    "new_expected_size": new_item.expected_size,
                    "existing_expected_size": existing.expected_size,
                    "new_expected_camera": new_item.expected_camera,
                    "existing_expected_camera": existing.expected_camera,
                }
                message = (
                    f"目标文件名 '{target_name}' 在多个清单中指向不同的源文件: "
                    f"新清单期望哈希={new_item.expected_hash or 'N/A'}, "
                    f"现有清单期望哈希={existing.expected_hash or 'N/A'}"
                )
                conflicts.append(
                    Conflict(
                        conflict_id=str(uuid.uuid4())[:8],
                        conflict_type=ConflictType.TARGET_NAME_CONFLICT,
                        target_name=target_name,
                        batch_name=batch_name,
                        message=message,
                        details=details,
                    )
                )

    return conflicts


def _detect_source_file_conflicts(
    new_items: List[DeliveryItem],
    existing_items: Dict[str, DeliveryItem],
    scanned_files: Dict,
    batch_name: str,
) -> List[Conflict]:
    conflicts: List[Conflict] = []

    def _find_source_by_hash(expected_hash: Optional[str]) -> Optional[str]:
        if not expected_hash:
            return None
        for sf in scanned_files.values():
            if sf.hash == expected_hash:
                return sf.original_path
        return None

    new_source_map: Dict[str, List[str]] = {}
    for new_item in new_items:
        src = _find_source_by_hash(new_item.expected_hash)
        if src:
            if src not in new_source_map:
                new_source_map[src] = []
            new_source_map[src].append(new_item.target_name)

    existing_source_map: Dict[str, List[str]] = {}
    for existing_item in existing_items.values():
        src = _find_source_by_hash(existing_item.expected_hash)
        if src:
            if src not in existing_source_map:
                existing_source_map[src] = []
            existing_source_map[src].append(existing_item.target_name)

    for src, new_targets in new_source_map.items():
        if src in existing_source_map:
            existing_targets = existing_source_map[src]
            all_targets = list(set(new_targets + existing_targets))
            if len(all_targets) > 1:
                details = {
                    "source_file": src,
                    "new_targets": new_targets,
                    "existing_targets": existing_targets,
                }
                message = (
                    f"源文件 '{src}' 被多个目标文件名抢占: "
                    f"{', '.join(all_targets)}"
                )
                conflicts.append(
                    Conflict(
                        conflict_id=str(uuid.uuid4())[:8],
                        conflict_type=ConflictType.SOURCE_FILE_CONFLICT,
                        source_file=src,
                        batch_name=batch_name,
                        message=message,
                        details=details,
                    )
                )

    return conflicts


def _detect_batch_name_conflicts(
    new_batch_name: Optional[str],
    existing_batches: List[Dict],
    current_batch_id: str,
) -> List[Conflict]:
    conflicts: List[Conflict] = []

    if not new_batch_name:
        return conflicts

    normalized_new = BatchHistory._normalize_name(new_batch_name)

    for existing in existing_batches:
        if existing["batch_id"] == current_batch_id:
            continue
        normalized_existing = BatchHistory._normalize_name(existing["name"])
        if normalized_new == normalized_existing and existing["name"] != new_batch_name:
            details = {
                "new_batch_name": new_batch_name,
                "existing_batch_name": existing["name"],
                "existing_batch_id": existing["batch_id"],
                "normalized_name": normalized_new,
            }
            message = (
                f"批次名 '{new_batch_name}' 归一后与现有批次 '{existing['name']}' (ID: {existing['batch_id']}) 冲突: "
                f"归一后均为 '{normalized_new}'"
            )
            conflicts.append(
                Conflict(
                    conflict_id=str(uuid.uuid4())[:8],
                    conflict_type=ConflictType.BATCH_NAME_CONFLICT,
                    batch_name=new_batch_name,
                    message=message,
                    details=details,
                )
            )

    return conflicts


def import_delivery_list(
    config: Config,
    storage: BatchStorage,
    manifest_path: str,
    batch_id: str = None,
    batch_name: str = None,
    merge: bool = True,
) -> Dict:
    batch, created = get_or_create_batch(storage, batch_id, batch_name)

    path = Path(manifest_path)
    if not path.exists():
        raise FileNotFoundError(f"清单文件不存在: {manifest_path}")

    new_items = _parse_manifest(path)

    existing_items = dict(batch.delivery_list) if merge else {}

    conflicts: List[Conflict] = []

    target_conflicts = _detect_target_name_conflicts(
        new_items, existing_items, batch.name
    )
    conflicts.extend(target_conflicts)

    source_conflicts = _detect_source_file_conflicts(
        new_items, existing_items, batch.scanned_files, batch.name
    )
    conflicts.extend(source_conflicts)

    existing_batches = storage.list_batches()
    name_conflicts = _detect_batch_name_conflicts(
        batch_name, existing_batches, batch.batch_id
    )
    conflicts.extend(name_conflicts)

    unresolved_existing = [c for c in batch.conflicts if not c.resolved]

    batch.conflicts = unresolved_existing + conflicts

    for item in new_items:
        if item.target_name in batch.delivery_list and any(
            c.target_name == item.target_name and c.conflict_type == ConflictType.TARGET_NAME_CONFLICT
            for c in conflicts
        ):
            continue
        batch.delivery_list[item.target_name] = item

    import_record = ImportRecord(
        import_id=str(uuid.uuid4())[:8],
        source_file=str(path.resolve()),
        imported_at=datetime.now(),
        imported_count=len(new_items),
        batch_name=batch.name,
        items=[item.to_dict() for item in new_items],
    )
    batch.import_records.append(import_record)

    if conflicts:
        batch.merge_status = MergeStatus.CONFLICT
    else:
        batch.merge_status = MergeStatus.OK

    batch.last_import_at = datetime.now()
    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "imported_count": len(new_items),
        "source_file": str(path.resolve()),
        "items": [item.to_dict() for item in new_items],
        "conflict_count": len(conflicts),
        "conflicts": [c.to_dict() for c in conflicts],
        "merge_status": batch.merge_status.value,
        "import_id": import_record.import_id,
        "total_delivery_items": len(batch.delivery_list),
    }
