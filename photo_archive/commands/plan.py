from pathlib import Path
from typing import Dict, List
from collections import defaultdict

from ..config import Config
from ..models import (
    BatchHistory,
    CorrectionAction,
    CorrectionType,
    FileStatus,
)
from ..storage import BatchStorage
from .utils import get_or_create_batch, ensure_archive_dir


def generate_correction_plan(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    if not batch.delivery_list:
        raise ValueError("请先导入交付清单")

    if not batch.scanned_files:
        raise ValueError("请先扫描源目录")

    archive_dir = ensure_archive_dir(config, batch.name)

    applied_corrections = [c for c in batch.corrections if c.applied and not c.rolled_back]
    rolled_back_corrections = [c for c in batch.corrections if c.rolled_back]
    applied_targets = {c.target for c in applied_corrections}
    applied_ids = {c.id for c in applied_corrections}
    rolled_back_targets = {c.target for c in rolled_back_corrections}

    name_to_sources: Dict[str, List[str]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        name_to_sources[sf.file_name].append(sf.original_path)

    hash_to_sources: Dict[str, List[str]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        hash_to_sources[sf.hash].append(sf.original_path)

    new_corrections: List[CorrectionAction] = []

    for item in batch.delivery_list.values():
        target_name = item.target_name
        target_path = str(archive_dir / target_name)

        if target_path in applied_targets:
            continue

        if item.status == FileStatus.OK and item.matched_source:
            if not Path(target_path).exists():
                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=item.matched_source,
                    target=target_path,
                    reason="将匹配的源文件复制到归档目录",
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

                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=src,
                    target=dup_target,
                    reason=f"重复文件: {'选择第一个源文件' if i == 0 else f'重命名保存第{i+1}个副本'}",
                ))

        elif item.status == FileStatus.HASH_MISMATCH and item.matched_source:
            mismatch_target = str(archive_dir / f"hash_mismatch_{target_name}")
            if mismatch_target not in applied_targets:
                new_corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=item.matched_source,
                    target=mismatch_target,
                    reason=f"哈希不一致: 保存原始文件供人工核验",
                ))

        elif item.status == FileStatus.MISSING:
            if target_path not in applied_targets:
                new_corrections.append(CorrectionAction(
                    type=CorrectionType.SKIP,
                    source="",
                    target=target_path,
                    reason=f"缺少文件: {target_name}",
                ))

    all_corrections = applied_corrections + rolled_back_corrections + new_corrections
    batch.corrections = all_corrections
    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "archive_dir": str(archive_dir),
        "correction_count": len(all_corrections),
        "pending_count": len(new_corrections),
        "applied_count": len(applied_corrections),
        "undone_count": len(rolled_back_corrections),
        "corrections": [c.to_dict() for c in all_corrections],
        "pending_corrections": [c.to_dict() for c in new_corrections],
    }
