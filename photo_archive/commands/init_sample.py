from pathlib import Path
from typing import Dict, List
import hashlib
import os
import shutil
import uuid


def generate_sample_data(
    sample_dir: str,
    num_cameras: int = 3,
    num_photos_per_camera: int = 5,
    batch_name: str = "WEDDING001",
) -> Dict[str, List[str]]:
    base_dir = Path(sample_dir)
    source_dir = base_dir / "source_cards"
    delivery_dir = base_dir / "delivery_list"

    if source_dir.exists():
        shutil.rmtree(source_dir)
    if delivery_dir.exists():
        shutil.rmtree(delivery_dir)

    source_dir.mkdir(parents=True, exist_ok=True)
    delivery_dir.mkdir(parents=True, exist_ok=True)

    cameras = ["A", "B", "C"][:num_cameras]
    source_files: Dict[str, List[str]] = {cam: [] for cam in cameras}
    expected_files: List[str] = []

    for cam in cameras:
        card_dir = source_dir / f"card_{cam}"
        card_dir.mkdir(parents=True, exist_ok=True)
        for seq in range(1, num_photos_per_camera + 1):
            content = f"photo-content-{cam}-{seq}-{uuid.uuid4()}".encode("utf-8")
            file_name = f"{cam}_{batch_name}_{seq:04d}.jpg"
            file_path = card_dir / file_name
            with open(file_path, "wb") as f:
                f.write(content)
            source_files[cam].append(str(file_path.resolve()))
            expected_files.append(f"{cam}_{batch_name}_{seq:04d}.jpg")

    missing_file = f"A_{batch_name}_{num_photos_per_camera + 1:04d}.jpg"
    expected_files.append(missing_file)

    duplicate_dir = source_dir / "card_A_duplicate"
    duplicate_dir.mkdir(parents=True, exist_ok=True)
    dup_content = f"duplicate-content-{uuid.uuid4()}".encode("utf-8")
    dup_file = duplicate_dir / f"A_{batch_name}_0001.jpg"
    with open(dup_file, "wb") as f:
        f.write(dup_content)
    source_files["A"].append(str(dup_file.resolve()))

    manifest_path = delivery_dir / "delivery_manifest.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        for target in expected_files:
            parts = target.replace(".jpg", "").split("_")
            cam = parts[0]
            seq = int(parts[2])
            f.write(f"{target},,{cam},{seq}\n")

    config_path = base_dir / "config.yaml"
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(f'naming_rule: "{{机位}}_{{批次}}_{{序号:04d}}.{{扩展名}}"\n')
        f.write("cameras:\n")
        for cam in cameras:
            f.write(f"  - {cam}\n")
        f.write("hash_strategy: sha256\n")
        f.write("archive_dir: ./archive\n")
        f.write("work_dir: ./work\n")

    return {
        "source_dir": str(source_dir),
        "delivery_manifest": str(manifest_path),
        "config": str(config_path),
        "source_files": source_files,
        "expected_files": expected_files,
    }


def create_corrupted_sample(file_path: str) -> None:
    path = Path(file_path)
    with open(path, "wb") as f:
        f.write(b"corrupted content that doesn't match original")
