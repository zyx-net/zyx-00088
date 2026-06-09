from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from collections import defaultdict
import uuid

from ..config import Config
from ..models import (
    BatchHistory,
    CorrectionAction,
    CorrectionStatus,
    CorrectionType,
    FileStatus,
    PlanSnapshot,
)
from ..scanner import FileScanner
from ..storage import BatchStorage
from .utils import get_or_create_batch, ensure_archive_dir


def generate_correction_plan(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    save_as: Optional[str] = None,
    snapshot_description: Optional[str] = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    if not batch.delivery_list:
        raise ValueError("请先导入交付清单")

    if not batch.scanned_files:
        raise ValueError("请先扫描源目录")

    archive_dir = ensure_archive_dir(config, batch.name)
    scanner = FileScanner(config)

    applied_corrections = [c for c in batch.corrections if c.applied and not c.rolled_back]
    rolled_back_corrections = [c for c in batch.corrections if c.rolled_back]
    applied_targets = {c.target for c in applied_corrections}

    name_to_sources: Dict[str, List[str]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        name_to_sources[sf.file_name].append(sf.original_path)

    new_corrections: List[CorrectionAction] = []

    for item in batch.delivery_list.values():
        target_name = item.target_name
        target_path = str(archive_dir / target_name)

        if target_path in applied_targets:
            continue

        if item.status == FileStatus.OK and item.matched_source:
            if not Path(target_path).exists():
                source_path = item.matched_source
                source_hash = None
                expected_target_hash = None
                if source_path and source_path in batch.scanned_files:
                    source_hash = batch.scanned_files[source_path].hash
                    expected_target_hash = source_hash

                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=source_path,
                    target=target_path,
                    reason="将匹配的源文件复制到归档目录",
                    source_hash=source_hash,
                    expected_target_hash=expected_target_hash,
                ))

        elif item.status == FileStatus.DUPLICATE:
            sources = name_to_sources.get(target_name, [])
            for i, src in enumerate(sources):
                if i == 0:
                    dup_target = target_path
                else:
                    dup_target = str(archive_dir / f"duplicate_{i}_{target_name}")

                if dup_target in applied_targets:
                    continue

                source_hash = None
                expected_target_hash = None
                if src in batch.scanned_files:
                    source_hash = batch.scanned_files[src].hash
                    expected_target_hash = source_hash

                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=src,
                    target=dup_target,
                    reason=f"重复文件: {'选择第一个源文件' if i == 0 else f'重命名保存第{i+1}个副本'}",
                    source_hash=source_hash,
                    expected_target_hash=expected_target_hash,
                ))

        elif item.status == FileStatus.HASH_MISMATCH and item.matched_source:
            mismatch_target = str(archive_dir / f"hash_mismatch_{target_name}")
            if mismatch_target not in applied_targets:
                source_path = item.matched_source
                source_hash = None
                expected_target_hash = None
                if source_path in batch.scanned_files:
                    source_hash = batch.scanned_files[source_path].hash
                    expected_target_hash = source_hash

                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=source_path,
                    target=mismatch_target,
                    reason=f"哈希不一致: 保存原始文件供人工核验",
                    source_hash=source_hash,
                    expected_target_hash=expected_target_hash,
                ))

        elif item.status == FileStatus.MISSING:
            if target_path not in applied_targets:
                new_corrections.append(CorrectionAction(
                    type=CorrectionType.SKIP,
                    source="",
                    target=target_path,
                    reason=f"缺少文件: {target_name}",
                    status=CorrectionStatus.SKIPPED,
                ))

    all_corrections = applied_corrections + rolled_back_corrections + new_corrections

    for c in all_corrections:
        if c.status == CorrectionStatus.PENDING and c.applied and not c.rolled_back:
            c.status = CorrectionStatus.COMPLETED
        elif c.rolled_back and c.status != CorrectionStatus.ROLLED_BACK:
            c.status = CorrectionStatus.ROLLED_BACK

    batch.corrections = all_corrections
    batch.last_plan_at = datetime.now()

    snapshot = None
    if save_as:
        snapshot_id = str(uuid.uuid4())[:8]
        snapshot = PlanSnapshot(
            snapshot_id=snapshot_id,
            name=save_as,
            created_at=datetime.now(),
            correction_ids=[c.id for c in new_corrections],
            archive_dir=str(archive_dir),
            description=snapshot_description or "",
        )
        batch.plan_snapshots.append(snapshot)
        batch.active_snapshot_id = snapshot_id

        for c in new_corrections:
            c.plan_snapshot_id = snapshot_id

    storage.save(batch)

    stats = batch.get_statistics()

    result = {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "archive_dir": str(archive_dir),
        "correction_count": len(all_corrections),
        "pending_count": stats["pending"],
        "applied_count": stats["completed"],
        "undone_count": stats["rolled_back"],
        "conflicted_count": stats["conflicted"],
        "failed_count": stats["failed"],
        "skipped_count": stats["skipped"],
        "undoable_count": stats["undoable"],
        "corrections": [c.to_dict() for c in all_corrections],
        "pending_corrections": [c.to_dict() for c in new_corrections],
        "statistics": stats,
    }

    if snapshot:
        result["snapshot_id"] = snapshot.snapshot_id
        result["snapshot_name"] = snapshot.name
        result["snapshot_correction_count"] = len(snapshot.correction_ids)

    return result
