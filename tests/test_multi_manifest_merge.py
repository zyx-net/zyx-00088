from pathlib import Path
import subprocess
import sys
import shutil
import os
import json
import time

import pytest


TEST_DIR = Path(__file__).parent.parent.resolve()
SAMPLE_DIR = TEST_DIR / "test_merge_data"
WORK_DIR = TEST_DIR / "test_merge_work"
ARCHIVE_DIR = TEST_DIR / "test_merge_archive"


def run_photo_archive(args, cwd=None, check=True):
    cmd = [sys.executable, "-m", "photo_archive.cli"] + args
    env = os.environ.copy()
    if cwd:
        env["PYTHONPATH"] = str(TEST_DIR)
    env["PYTHONIOENCODING"] = "utf-8"
    result = subprocess.run(
        cmd,
        cwd=cwd or TEST_DIR,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    if result.stdout is None:
        result.stdout = ""
    if result.stderr is None:
        result.stderr = ""
    return result


@pytest.fixture(autouse=True)
def setup_and_teardown():
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)
    yield
    for d in [SAMPLE_DIR, WORK_DIR, ARCHIVE_DIR]:
        if d.exists():
            shutil.rmtree(d)


def create_config(config_path, work_dir, archive_dir, cameras=None):
    with open(config_path, "w", encoding="utf-8") as f:
        f.write('naming_rule: "{机位}_{批次}_{序号:04d}.{扩展名}"\n')
        f.write("cameras:\n")
        for cam in (cameras or ["A", "B"]):
            f.write(f"  - {cam}\n")
        f.write("hash_strategy: sha256\n")
        f.write(f"archive_dir: {archive_dir}\n")
        f.write(f"work_dir: {work_dir}\n")


def create_source_file(card_dir, filename, content):
    card_dir.mkdir(parents=True, exist_ok=True)
    file_path = card_dir / filename
    with open(file_path, "wb") as f:
        f.write(content)
    return file_path


def get_file_hash(file_path):
    import hashlib
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def test_multi_manifest_import_merge():
    """测试多清单导入合并功能"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
        "--batch", "MERGE001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A", "B"])

    source_files = list(source_dir.glob("*.jpg"))
    assert len(source_files) == 2

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest_batch1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_MERGE001_0001.jpg,A,1\n")
        f.write("A_MERGE001_0002.jpg,A,2\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest_batch2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("B_MERGE001_0001.jpg,B,1\n")
        f.write("B_MERGE001_0002.jpg,B,2\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(SAMPLE_DIR / "source_cards"),
        "--batch-name", "merge-test",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--json",
    ])
    assert result.returncode == 0, f"import1 failed: {result.stderr}"
    import1_data = json.loads(result.stdout)
    assert import1_data["imported_count"] == 2
    assert import1_data["conflict_count"] == 0
    assert import1_data["merge_status"] == "ok"
    assert import1_data["total_delivery_items"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--json",
    ])
    assert result.returncode == 0, f"import2 failed: {result.stderr}"
    import2_data = json.loads(result.stdout)
    assert import2_data["imported_count"] == 2
    assert import2_data["conflict_count"] == 0
    assert import2_data["merge_status"] == "ok"
    assert import2_data["total_delivery_items"] == 4

    batch_id = import2_data["batch_id"]
    report_path = SAMPLE_DIR / "merge_report.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--output", str(report_path),
        "--format", "json",
    ])
    assert result.returncode in [0, 4, 5]

    with open(report_path, "r", encoding="utf-8") as f:
        report_data = json.load(f)

    assert report_data["summary"]["total_delivery_items"] == 4
    assert report_data["summary"]["total_imports"] == 2
    assert len(report_data["import_records"]) == 2
    assert report_data["summary"]["merge_status"] == "ok"
    assert report_data["summary"]["unresolved_conflicts_count"] == 0


def test_target_name_conflict_detection():
    """测试目标文件名冲突检测 - 同一目标文件名指向不同源文件"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "TARGETCONF",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    source_files = sorted(source_dir.glob("*.jpg"))
    assert len(source_files) == 2
    hash1 = get_file_hash(source_files[0])
    hash2 = get_file_hash(source_files[1])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_TARGETCONF_0001.jpg,{hash1},A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_TARGETCONF_0001.jpg,{hash2},A,1\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--json",
    ])
    assert result.returncode == 7, f"Expected exit code 7 for conflict, got {result.returncode}"
    conflict_data = json.loads(result.stdout)
    assert conflict_data["conflict_count"] >= 1
    assert conflict_data["merge_status"] == "conflict"

    target_conflicts = [
        c for c in conflict_data["conflicts"]
        if c["conflict_type"] == "target_name_conflict"
    ]
    assert len(target_conflicts) >= 1
    assert "A_TARGETCONF_0001.jpg" in target_conflicts[0]["target_name"]
    assert hash1 in target_conflicts[0]["message"]
    assert hash2 in target_conflicts[0]["message"]


def test_source_file_conflict_detection():
    """测试源文件冲突检测 - 同一源文件被不同目标抢占"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "1",
        "--batch", "SRCCONF",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    source_files = list(source_dir.glob("*.jpg"))
    assert len(source_files) == 1
    file_hash = get_file_hash(source_files[0])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_SRCCONF_0001.jpg,{file_hash},A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_SRCCONF_0002.jpg,{file_hash},A,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])

    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path)])
    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--json",
    ])
    assert result.returncode == 7, f"Expected exit code 7 for conflict, got {result.returncode}"
    conflict_data = json.loads(result.stdout)

    source_conflicts = [
        c for c in conflict_data["conflicts"]
        if c["conflict_type"] == "source_file_conflict"
    ]
    assert len(source_conflicts) >= 1
    assert "A_SRCCONF_0001.jpg" in source_conflicts[0]["message"]
    assert "A_SRCCONF_0002.jpg" in source_conflicts[0]["message"]


def test_batch_name_conflict_detection():
    """测试批次名大小写或空格归一后冲突"""
    from photo_archive.models import BatchHistory, ConflictType
    from photo_archive.commands.import_list import _detect_batch_name_conflicts

    normalized1 = BatchHistory._normalize_name("Wedding 2024")
    normalized2 = BatchHistory._normalize_name("wedding 2024")
    normalized3 = BatchHistory._normalize_name("wedding  2024")
    normalized4 = BatchHistory._normalize_name("WEDDING2024")

    assert normalized1 == normalized2 == normalized3
    assert normalized1 != normalized4

    existing_batches = [
        {"batch_id": "batch1", "name": "Wedding 2024"},
        {"batch_id": "batch2", "name": "Birthday 2024"},
    ]

    conflicts = _detect_batch_name_conflicts("wedding 2024", existing_batches, "batch2")
    assert len(conflicts) == 1
    assert conflicts[0].conflict_type == ConflictType.BATCH_NAME_CONFLICT
    assert "wedding 2024" in conflicts[0].message.lower()
    assert "wedding 2024" in conflicts[0].details["normalized_name"]

    conflicts = _detect_batch_name_conflicts("Wedding 2024", existing_batches, "batch2")
    assert len(conflicts) == 0

    conflicts = _detect_batch_name_conflicts("wedding  2024", existing_batches, "batch2")
    assert len(conflicts) == 1

    conflicts = _detect_batch_name_conflicts("Birthday 2024", existing_batches, "batch2")
    assert len(conflicts) == 0

    conflicts = _detect_batch_name_conflicts("Other Event", existing_batches, "batch2")
    assert len(conflicts) == 0


def test_plan_apply_undo_blocked_by_conflicts():
    """测试存在冲突时 plan/apply/undo 被阻止"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "BLOCKTEST",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    source_files = sorted(source_dir.glob("*.jpg"))
    hash1 = get_file_hash(source_files[0])
    hash2 = get_file_hash(source_files[1])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_BLOCKTEST_0001.jpg,{hash1},A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_BLOCKTEST_0001.jpg,{hash2},A,1\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path)])
    result = run_photo_archive(["-c", str(config_path), "import-list", str(manifest2_path)])
    assert result.returncode == 7

    result = run_photo_archive(["-c", str(config_path), "plan"])
    assert result.returncode == 7, f"plan should be blocked by conflicts, got {result.returncode}"
    assert "未解决的冲突" in result.stderr

    result = run_photo_archive(["-c", str(config_path), "apply"])
    assert result.returncode == 7, f"apply should be blocked by conflicts, got {result.returncode}"
    assert "未解决的冲突" in result.stderr

    result = run_photo_archive(["-c", str(config_path), "undo"])
    assert result.returncode == 7, f"undo should be blocked by conflicts, got {result.returncode}"
    assert "未解决的冲突" in result.stderr


def test_import_records_persist_after_restart():
    """测试导入记录跨重启保留"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "3",
        "--batch", "PERSIST001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_PERSIST001_0001.jpg,A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_PERSIST001_0002.jpg,A,2\n")

    manifest3_path = SAMPLE_DIR / "delivery_list" / "manifest3.csv"
    with open(manifest3_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_PERSIST001_0003.jpg,A,3\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "persist-test",
        "--json",
    ])
    assert result.returncode == 0
    scan_data = json.loads(result.stdout)
    batch_id = scan_data["batch_id"]

    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path), "--batch-id", batch_id])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest2_path), "--batch-id", batch_id])

    time.sleep(0.1)

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report1 = json.loads(result.stdout)
    assert len(report1["import_records"]) == 2
    assert report1["summary"]["total_imports"] == 2
    assert report1["summary"]["total_delivery_items"] == 2

    run_photo_archive(["-c", str(config_path), "import-list", str(manifest3_path), "--batch-id", batch_id])

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report2 = json.loads(result.stdout)
    assert len(report2["import_records"]) == 3
    assert report2["summary"]["total_imports"] == 3
    assert report2["summary"]["total_delivery_items"] == 3

    import_ids_before = {r["import_id"] for r in report2["import_records"]}
    assert len(import_ids_before) == 3

    result = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    batches = json.loads(result.stdout)
    assert len(batches) == 1
    assert batches[0]["batch_id"] == batch_id
    assert batches[0]["delivery_count"] == 3


def test_config_change_between_imports():
    """测试配置变更后导入行为正确"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "2",
        "--photos", "2",
        "--batch", "CFGCHANGE",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A", "B"])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest_a.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_CFGCHANGE_0001.jpg,A,1\n")
        f.write("A_CFGCHANGE_0002.jpg,A,2\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest_b.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("B_CFGCHANGE_0001.jpg,B,1\n")
        f.write("B_CFGCHANGE_0002.jpg,B,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir), "--batch-name", "cfg-test"])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path)])

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--json",
    ])
    report1 = json.loads(result.stdout)
    assert report1["summary"]["total_delivery_items"] == 2

    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A", "B", "C"])

    run_photo_archive(["-c", str(config_path), "import-list", str(manifest2_path)])

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--json",
    ])
    report2 = json.loads(result.stdout)
    assert report2["summary"]["total_delivery_items"] == 4
    assert report2["summary"]["total_imports"] == 2


def test_undo_rollback_report_state():
    """测试撤销后 report 状态正确还原"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "UNDOTEST",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    manifest_path = SAMPLE_DIR / "delivery_list" / "manifest.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_UNDOTEST_0001.jpg,A,1\n")
        f.write("A_UNDOTEST_0002.jpg,A,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir), "--batch-name", "undo-test"])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest_path)])

    verify_result = run_photo_archive(["-c", str(config_path), "verify", "--json"])
    assert verify_result.returncode == 0
    verify_data = json.loads(verify_result.stdout)
    assert verify_data["ok_count"] == 2

    plan_result = run_photo_archive(["-c", str(config_path), "plan", "--json"])
    assert plan_result.returncode == 0
    plan_data = json.loads(plan_result.stdout)
    assert plan_data["correction_count"] >= 2

    apply_result = run_photo_archive(["-c", str(config_path), "apply", "--json"])
    assert apply_result.returncode == 0
    apply_data = json.loads(apply_result.stdout)
    assert apply_data["applied_count"] == 2

    archive_dir = ARCHIVE_DIR / "undo-test"
    archived_files = list(archive_dir.glob("*.jpg"))
    assert len(archived_files) == 2

    report_before_path = SAMPLE_DIR / "report_before_undo.json"
    run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_before_path),
        "--format", "json",
    ])
    with open(report_before_path, "r", encoding="utf-8") as f:
        report_before = json.load(f)
    assert report_before["summary"]["active_corrections_count"] == 2
    assert report_before["summary"]["undone_corrections_count"] == 0
    assert report_before["summary"]["ok_count"] == 2
    assert len(report_before["applied_corrections"]) == 2

    undo_result = run_photo_archive(["-c", str(config_path), "undo", "--json"])
    assert undo_result.returncode == 0
    undo_data = json.loads(undo_result.stdout)
    assert undo_data["undone_count"] == 2

    archived_files_after = list(archive_dir.glob("*.jpg"))
    assert len(archived_files_after) == 0

    report_after_path = SAMPLE_DIR / "report_after_undo.json"
    run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(report_after_path),
        "--format", "json",
    ])
    with open(report_after_path, "r", encoding="utf-8") as f:
        report_after = json.load(f)
    assert report_after["summary"]["active_corrections_count"] == 0
    assert report_after["summary"]["undone_corrections_count"] == 2
    assert report_after["summary"]["ok_count"] == 2
    assert len(report_after["applied_corrections"]) == 0
    assert len(report_after["undone_corrections"]) == 2
    assert len(report_after["import_records"]) == 1

    assert report_before["summary"]["ok_count"] == report_after["summary"]["ok_count"]
    assert report_before["summary"]["missing_count"] == report_after["summary"]["missing_count"]
    assert report_before["summary"]["duplicate_count"] == report_after["summary"]["duplicate_count"]
    assert report_before["summary"]["total_imports"] == report_after["summary"]["total_imports"]


def test_conflict_report_json_csv():
    """测试冲突报告支持 JSON 和 CSV 格式"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "REPTEST",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    source_files = sorted(source_dir.glob("*.jpg"))
    hash1 = get_file_hash(source_files[0])
    hash2 = get_file_hash(source_files[1])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_REPTEST_0001.jpg,{hash1},A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_REPTEST_0001.jpg,{hash2},A,1\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest2_path)])

    json_report_path = SAMPLE_DIR / "conflict_report.json"
    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(json_report_path),
        "--format", "json",
    ])
    assert result.returncode == 7
    assert json_report_path.exists()

    with open(json_report_path, "r", encoding="utf-8") as f:
        json_report = json.load(f)
    assert json_report["summary"]["unresolved_conflicts_count"] >= 1
    assert len(json_report["conflicts"]) >= 1
    assert "target_name_conflict" in [c["conflict_type"] for c in json_report["conflicts"]]

    csv_report_path = SAMPLE_DIR / "conflict_report.csv"
    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--output", str(csv_report_path),
        "--format", "csv",
    ])
    assert result.returncode == 7
    assert csv_report_path.exists()

    with open(csv_report_path, "r", encoding="utf-8-sig") as f:
        csv_content = f.read()
    assert "=== 冲突列表 ===" in csv_content
    assert "target_name_conflict" in csv_content
    assert "A_REPTEST_0001.jpg" in csv_content
