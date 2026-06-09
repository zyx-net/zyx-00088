from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

from ..config import Config
from ..models import BatchHistory, FileStatus, ScannedFile
from ..scanner import FileScanner
from ..storage import BatchStorage
from .utils import get_or_create_batch


def verify_batch(
    config: Config,
    storage: BatchStorage,
    batch_id: str = None,
    check_hashes: bool = True,
) -> Dict:
    batch, _ = get_or_create_batch(storage, batch_id)

    if not batch.delivery_list:
        raise ValueError("请先导入交付清单")

    if not batch.scanned_files:
        raise ValueError("请先扫描源目录")

    scanner = FileScanner(config)

    missing: List[str] = []
    duplicates: List[Dict] = []
    hash_mismatches: List[Dict] = []
    ok_count = 0

    hash_to_sources: Dict[str, List[ScannedFile]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        hash_to_sources[sf.hash].append(sf)

    name_to_sources: Dict[str, List[ScannedFile]] = defaultdict(list)
    for sf in batch.scanned_files.values():
        name_to_sources[sf.file_name].append(sf)

    def _find_matching_sources(target_name: str) -> List[ScannedFile]:
        exact_match = name_to_sources.get(target_name, [])
        if exact_match:
            return exact_match

        target_fields = scanner.extract_fields(target_name)
        target_cam = target_fields.get("机位")
        target_seq = target_fields.get("序号")
        target_ext = target_fields.get("扩展名")

        field_matches: List[ScannedFile] = []
        for sf in batch.scanned_files.values():
            sf_fields = scanner.extract_fields(sf.file_name)
            sf_cam = sf_fields.get("机位")
            sf_seq = sf_fields.get("序号")
            sf_ext = sf_fields.get("扩展名")

            cam_match = (target_cam is None or sf_cam is None or target_cam == sf_cam)
            seq_match = (target_seq is None or sf_seq is None or target_seq == sf_seq)
            ext_match = (target_ext is None or sf_ext is None or target_ext == sf_ext)

            if cam_match and seq_match and ext_match:
                field_matches.append(sf)

        return field_matches

    for item in batch.delivery_list.values():
        item.status = FileStatus.UNKNOWN
        item.matched_source = None
        item.actual_hash = None

        matching_by_name = _find_matching_sources(item.target_name)

        if not matching_by_name:
            item.status = FileStatus.MISSING
            missing.append(item.target_name)
            continue

        if len(matching_by_name) > 1:
            duplicates.append({
                "target_name": item.target_name,
                "count": len(matching_by_name),
                "sources": [sf.original_path for sf in matching_by_name],
            })
            item.status = FileStatus.DUPLICATE
            continue

        matched = matching_by_name[0]
        item.matched_source = matched.original_path
        item.actual_hash = matched.hash

        if check_hashes and item.expected_hash and item.expected_hash != matched.hash:
            hash_mismatches.append({
                "target_name": item.target_name,
                "source_path": matched.original_path,
                "expected_hash": item.expected_hash,
                "actual_hash": matched.hash,
            })
            item.status = FileStatus.HASH_MISMATCH
            continue

        item.status = FileStatus.OK
        ok_count += 1

    batch.last_verify_at = datetime.now()
    storage.save(batch)

    has_issues = bool(missing or duplicates or hash_mismatches)

    return {
        "batch_id": batch.batch_id,
        "batch_name": batch.name,
        "total_delivery": len(batch.delivery_list),
        "total_scanned": len(batch.scanned_files),
        "ok_count": ok_count,
        "missing_count": len(missing),
        "duplicate_count": len(duplicates),
        "hash_mismatch_count": len(hash_mismatches),
        "missing": missing,
        "duplicates": duplicates,
        "hash_mismatches": hash_mismatches,
        "has_issues": has_issues,
    }
