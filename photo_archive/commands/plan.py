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

    name_to_sources: Dict[str, List[str]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        name_to_sources[sf.file_name].append(sf.original_path)

    hash_to_sources: Dict[str, List[str]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        hash_to_sources[sf.hash].append(sf.original_path)

    corrections: List[CorrectionAction] = []

    for item in batch.delivery_list.values():
        target_name = item.target_name
        target_path = str(archive_dir / target_name)

        if item.status == FileStatus.OK and item.matched_source:
            if not Path(target_path).exists():
                corrections.append(CorrectionAction(
                    type=CorrectionType.COPY,
                    source=item.matched_source,
                    target=target_path,
                    reason="将匹配的源文件复制到归档目录",
                ))

        elif item.status == FileStatus.DUPLICATE:
            sources = name_to_sources.get(target_name, [])
            for i, src in enumerate(sources):
                if i == 0:
                    corrections.append(CorrectionAction(
                        type=CorrectionType.COPY,
                        source=src,
                        target=target_path,
                        reason=f"重复文件: 选择第一个源文件",
                    ))
                else:
                    dup_target = str(archive_dir / f"duplicate_{i}_{target_name}")
                    corrections.append(CorrectionAction(
                        type=CorrectionType.COPY,
                        source=src,
                        target=dup_target,
                        reason=f"重复文件: 重命名保存第{i+1}个副本",
                    ))

        elif item.status == FileStatus.HASH_MISMATCH and item.matched_source:
            mismatch_target = str(archive_dir / f"hash_mismatch_{target_name}")
            corrections.append(CorrectionAction(
                type=CorrectionType.COPY,
                source=item.matched_source,
                target=mismatch_target,
                reason=f"哈希不一致: 保存原始文件供人工核验",
            ))

        elif item.status == FileStatus.MISSING:
            corrections.append(CorrectionAction(
                type=CorrectionType.SKIP,
                source="",
                target=target_path,
                reason=f"缺少文件: {target_name}",
            ))

    batch.corrections = corrections
    storage.save(batch)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "archive_dir": str(archive_dir),
        "correction_count": len(corrections),
        "corrections": [c.to_dict() for c in corrections],
    }
