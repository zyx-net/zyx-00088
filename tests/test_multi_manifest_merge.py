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


def test_scan_batch_name_case_conflict_via_cli():
    """测试 scan 命令通过 CLI 检测批次名大小写冲突"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST001_0001.jpg", b"test1")

    result1 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Wedding 2024",
        "--json",
    ])
    assert result1.returncode == 0, f"第一次 scan 失败: {result1.stderr}"
    data1 = json.loads(result1.stdout)
    batch_id1 = data1["batch_id"]
    assert data1["batch_name"] == "Wedding 2024"

    result2 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "wedding  2024",
        "--json",
    ])
    assert result2.returncode == 8, f"应该返回退出码 8，实际: {result2.returncode}, stderr: {result2.stderr}"
    conflict_data = json.loads(result2.stdout)
    assert conflict_data["error"] == "batch_name_conflict"
    assert conflict_data["requested_name"] == "wedding  2024"
    assert conflict_data["normalized_name"] == "wedding 2024"
    assert len(conflict_data["conflicting_batches"]) == 1
    assert conflict_data["conflicting_batches"][0]["batch_id"] == batch_id1
    assert conflict_data["conflicting_batches"][0]["name"] == "Wedding 2024"

    result3 = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    assert result3.returncode == 0
    batches = json.loads(result3.stdout)
    assert len(batches) == 1
    assert batches[0]["batch_id"] == batch_id1
    assert batches[0]["name"] == "Wedding 2024"


def test_scan_batch_name_space_conflict_via_cli():
    """测试 scan 命令通过 CLI 检测批次名连续空格冲突"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST002_0001.jpg", b"test2")

    result1 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Summer   Trip",
        "--json",
    ])
    assert result1.returncode == 0, f"第一次 scan 失败: {result1.stderr}"
    data1 = json.loads(result1.stdout)
    batch_id1 = data1["batch_id"]

    result2 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Summer Trip",
        "--json",
    ])
    assert result2.returncode == 8, f"应该返回退出码 8，实际: {result2.returncode}, stderr: {result2.stderr}"
    conflict_data = json.loads(result2.stdout)
    assert conflict_data["normalized_name"] == "summer trip"
    assert len(conflict_data["conflicting_batches"]) == 1
    assert conflict_data["conflicting_batches"][0]["batch_id"] == batch_id1

    result3 = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    assert result3.returncode == 0
    batches = json.loads(result3.stdout)
    assert len(batches) == 1
    assert batches[0]["name"] == "Summer   Trip"


def test_scan_exact_name_match_reuses_batch():
    """测试完全相同的批次名正确复用现有批次"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir1 = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir2 = SAMPLE_DIR / "source_cards" / "card_B"
    source_dir1.mkdir(parents=True, exist_ok=True)
    source_dir2.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A", "B"])
    create_source_file(source_dir1, "A_TEST003_0001.jpg", b"test3a")
    create_source_file(source_dir2, "B_TEST003_0001.jpg", b"test3b")

    result1 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir1),
        "--batch-name", "MyBatch",
        "--json",
    ])
    assert result1.returncode == 0
    data1 = json.loads(result1.stdout)
    batch_id1 = data1["batch_id"]

    result2 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir2),
        "--batch-name", "MyBatch",
        "--json",
    ])
    assert result2.returncode == 0, f"同名批次应该复用，实际: {result2.returncode}, stderr: {result2.stderr}"
    data2 = json.loads(result2.stdout)
    assert data2["batch_id"] == batch_id1
    assert data2["batch_name"] == "MyBatch"
    assert data2["scanned_count"] == 1

    result3 = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    assert result3.returncode == 0
    batches = json.loads(result3.stdout)
    assert len(batches) == 1
    assert batches[0]["file_count"] == 2


def test_import_list_batch_name_conflict_via_cli():
    """测试 import-list 命令通过 CLI 检测批次名冲突"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST004_0001.jpg", b"test4")

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_TEST004_0001.jpg,A,1\n")

    run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Christmas 2024",
    ])

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
        "--batch-name", "christmas  2024",
        "--json",
    ])
    assert result.returncode == 8, f"应该返回退出码 8，实际: {result.returncode}, stderr: {result.stderr}"
    conflict_data = json.loads(result.stdout)
    assert conflict_data["error"] == "batch_name_conflict"
    assert conflict_data["requested_name"] == "christmas  2024"
    assert conflict_data["normalized_name"] == "christmas 2024"


def test_batch_name_conflict_report_audit():
    """测试批次名冲突在报告和审计中的用户可见结果"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST005_0001.jpg", b"test5")

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = manifest_dir / "manifest.csv"
    with open(manifest_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_TEST005_0001.jpg,A,1\n")

    result1 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "New Year 2025",
        "--json",
    ])
    assert result1.returncode == 0
    data1 = json.loads(result1.stdout)
    batch_id1 = data1["batch_id"]

    run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest_path),
        "--batch-id", batch_id1,
    ])

    result2 = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id1,
        "--json",
    ])
    assert result2.returncode in [0, 4, 5]
    report_data = json.loads(result2.stdout)
    assert report_data["summary"]["batch_name"] == "New Year 2025"
    assert report_data["summary"]["normalized_name"] == "new year 2025"
    assert report_data["summary"]["total_delivery_items"] == 1

    result3 = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id1,
        "--output", str(SAMPLE_DIR / "audit_report.csv"),
        "--format", "csv",
    ])
    assert result3.returncode in [0, 4, 5]
    with open(SAMPLE_DIR / "audit_report.csv", "r", encoding="utf-8-sig") as f:
        csv_content = f.read()
    assert "New Year 2025" in csv_content
    assert "=== 导入记录 ===" in csv_content


def test_batch_name_conflict_text_output():
    """测试批次名冲突的文本输出可读性"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST006_0001.jpg", b"test6")

    run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Spring Festival",
    ])

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "spring   festival",
    ])
    assert result.returncode == 8
    assert "批次名冲突" in result.stderr
    assert "Spring Festival" in result.stderr
    assert "归一化后名称" in result.stderr
    assert "spring festival" in result.stderr


def test_batch_id_rename_with_conflict_check():
    """测试通过 --batch-id 重命名批次时也进行冲突检查"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])
    create_source_file(source_dir, "A_TEST007_0001.jpg", b"test7a")
    create_source_file(source_dir, "A_TEST007_0002.jpg", b"test7b")

    result1 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "BatchAlpha",
        "--json",
    ])
    assert result1.returncode == 0
    data1 = json.loads(result1.stdout)
    batch_id1 = data1["batch_id"]

    result2 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "Batch Beta",
        "--json",
    ])
    assert result2.returncode == 0

    result3 = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-id", batch_id1,
        "--batch-name", "batch  beta",
        "--json",
    ])
    assert result3.returncode == 8, f"重命名时应该检测冲突，实际: {result3.returncode}, stderr: {result3.stderr}"
    conflict_data = json.loads(result3.stdout)
    assert conflict_data["error"] == "batch_name_conflict"
    assert conflict_data["requested_name"] == "batch  beta"
    assert conflict_data["normalized_name"] == "batch beta"

    result4 = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    assert result4.returncode == 0
    batches = json.loads(result4.stdout)
    batch1 = next(b for b in batches if b["batch_id"] == batch_id1)
    assert batch1["name"] == "BatchAlpha"


def test_import_list_dry_run_does_not_modify_history():
    """测试 dry-run 预览模式不修改批次历史，跨重启后批次历史不变"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "3",
        "--batch", "DRYRUN001",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_DRYRUN001_0001.jpg,A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,camera,sequence\n")
        f.write("A_DRYRUN001_0002.jpg,A,2\n")
        f.write("A_DRYRUN001_0003.jpg,A,3\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "dryrun-test",
        "--json",
    ])
    assert result.returncode == 0
    scan_data = json.loads(result.stdout)
    batch_id = scan_data["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    import1_data = json.loads(result.stdout)
    assert import1_data["total_delivery_items"] == 1
    assert import1_data["added_count"] == 1
    assert import1_data["dry_run"] is False

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report_before_dryrun = json.loads(result.stdout)
    assert len(report_before_dryrun["import_records"]) == 1
    assert report_before_dryrun["summary"]["total_delivery_items"] == 1
    import_records_before = report_before_dryrun["import_records"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--batch-id", batch_id,
        "--dry-run",
        "--json",
    ])
    assert result.returncode == 0, f"dry-run 失败: {result.stderr}"
    dryrun_data = json.loads(result.stdout)
    assert dryrun_data["dry_run"] is True
    assert dryrun_data["dry_run_note"] is not None
    assert dryrun_data["added_count"] == 2
    assert dryrun_data["total_delivery_items"] == 1
    assert dryrun_data["import_record"]["dry_run"] is True
    assert "A_DRYRUN001_0002.jpg" in dryrun_data["added_items"]
    assert "A_DRYRUN001_0003.jpg" in dryrun_data["added_items"]

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report_after_dryrun = json.loads(result.stdout)
    assert len(report_after_dryrun["import_records"]) == 1
    assert report_after_dryrun["summary"]["total_delivery_items"] == 1
    import_records_after = report_after_dryrun["import_records"]
    assert import_records_before == import_records_after

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0
    real_import_data = json.loads(result.stdout)
    assert real_import_data["dry_run"] is False
    assert real_import_data["total_delivery_items"] == 3
    assert real_import_data["added_count"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report_final = json.loads(result.stdout)
    assert len(report_final["import_records"]) == 2
    assert report_final["summary"]["total_delivery_items"] == 3
    assert report_final["import_records"][-1]["dry_run"] is False
    assert report_final["import_records"][-1]["source_file"] == str(manifest2_path.resolve())


def test_import_list_conflict_details_readable():
    """测试冲突明细在文本和JSON输出中都可读"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "2",
        "--batch", "CONFLICT01",
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
        f.write(f"A_CONFLICT01_0001.jpg,{hash1},A,1\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_CONFLICT01_0001.jpg,{hash2},A,1\n")
        f.write(f"A_CONFLICT01_0002.jpg,{hash2},A,2\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])
    run_photo_archive(["-c", str(config_path), "import-list", str(manifest1_path)])

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--dry-run",
        "--json",
    ])
    assert result.returncode == 7, f"应该返回退出码 7，实际: {result.returncode}"
    json_data = json.loads(result.stdout)
    assert json_data["conflict_count"] >= 1
    assert json_data["conflicted_count"] >= 1
    assert "A_CONFLICT01_0001.jpg" in json_data["conflicted_items"]
    assert "A_CONFLICT01_0001.jpg" in json_data["import_record"]["conflicted_items"]
    assert json_data["import_record"]["dry_run"] is True

    conflict_summary = json_data["import_record"]["conflict_summary"]
    assert len(conflict_summary["target_name_conflicts"]) >= 1
    assert "A_CONFLICT01_0001.jpg" in conflict_summary["target_name_conflicts"][0]["target_name"]
    assert hash1 in conflict_summary["target_name_conflicts"][0]["message"]
    assert hash2 in conflict_summary["target_name_conflicts"][0]["message"]

    result_text = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--dry-run",
    ])
    assert result_text.returncode == 7
    assert "[PREVIEW]" in result_text.stdout
    assert "未写入批次历史" in result_text.stdout
    assert "冲突明细:" in result_text.stdout
    assert "! A_CONFLICT01_0001.jpg" in result_text.stdout
    assert "冲突列表:" in result_text.stdout
    assert "target_name_conflict" in result_text.stdout
    assert hash1 in result_text.stdout
    assert hash2 in result_text.stdout


def test_import_list_allow_overwrite_report():
    """测试开启 --allow-overwrite 后报告内容正确，默认保护旧数据"""
    run_photo_archive([
        "init-sample", str(SAMPLE_DIR),
        "--cameras", "1",
        "--photos", "3",
        "--batch", "OVERWRITE01",
    ])

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    source_files = sorted(source_dir.glob("*.jpg"))
    assert len(source_files) == 3
    hash1 = get_file_hash(source_files[0])
    hash2 = get_file_hash(source_files[1])
    hash3 = get_file_hash(source_files[2])

    manifest1_path = SAMPLE_DIR / "delivery_list" / "manifest1.csv"
    with open(manifest1_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_OVERWRITE01_0001.jpg,{hash1},A,1\n")
        f.write(f"A_OVERWRITE01_0002.jpg,{hash2},A,2\n")

    manifest2_path = SAMPLE_DIR / "delivery_list" / "manifest2.csv"
    with open(manifest2_path, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_OVERWRITE01_0002.jpg,{hash3},A,2\n")
        f.write(f"A_OVERWRITE01_0003.jpg,{hash3},A,3\n")

    run_photo_archive(["-c", str(config_path), "scan", str(source_dir)])

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--json",
    ])
    assert result.returncode == 0
    import1_data = json.loads(result.stdout)
    batch_id = import1_data["batch_id"]
    assert import1_data["added_count"] == 2
    assert import1_data["total_delivery_items"] == 2

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--dry-run",
        "--json",
    ])
    assert result.returncode == 7
    dryrun_data = json.loads(result.stdout)
    assert dryrun_data["conflicted_count"] == 1
    assert "A_OVERWRITE01_0002.jpg" in dryrun_data["conflicted_items"]
    assert dryrun_data["added_count"] == 1
    assert "A_OVERWRITE01_0003.jpg" in dryrun_data["added_items"]
    assert dryrun_data["skipped_count"] == 0
    assert dryrun_data["overwritten_count"] == 0
    assert dryrun_data["allow_overwrite"] is False

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--dry-run",
        "--allow-overwrite",
        "--json",
    ])
    assert result.returncode == 0, f"允许覆盖时应该返回 0，实际: {result.returncode}, stderr: {result.stderr}"
    dryrun_overwrite = json.loads(result.stdout)
    assert dryrun_overwrite["allow_overwrite"] is True
    assert dryrun_overwrite["overwritten_count"] == 1
    assert "A_OVERWRITE01_0002.jpg" in dryrun_overwrite["overwritten_items"]
    assert dryrun_overwrite["added_count"] == 1
    assert "A_OVERWRITE01_0003.jpg" in dryrun_overwrite["added_items"]
    assert dryrun_overwrite["conflicted_count"] == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2_path),
        "--allow-overwrite",
        "--json",
    ])
    assert result.returncode == 0
    real_overwrite = json.loads(result.stdout)
    assert real_overwrite["allow_overwrite"] is True
    assert real_overwrite["dry_run"] is False
    assert real_overwrite["overwritten_count"] == 1
    assert real_overwrite["added_count"] == 1
    assert real_overwrite["total_delivery_items"] == 3
    assert real_overwrite["import_record"]["dry_run"] is False
    assert real_overwrite["import_record"]["source_file"] == str(manifest2_path.resolve())
    assert len(real_overwrite["import_record"]["overwritten_items"]) == 1
    assert "A_OVERWRITE01_0002.jpg" in real_overwrite["import_record"]["overwritten_items"]

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode in [0, 4, 5]
    report_final = json.loads(result.stdout)
    assert len(report_final["import_records"]) == 2
    assert report_final["import_records"][-1]["dry_run"] is False
    assert report_final["import_records"][-1]["allow_overwrite"] is True if "allow_overwrite" in report_final["import_records"][-1] else True
    assert len(report_final["import_records"][-1]["overwritten_items"]) == 1
    assert "A_OVERWRITE01_0002.jpg" in report_final["import_records"][-1]["overwritten_items"]

    result_text = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1_path),
        "--dry-run",
    ])
    assert result_text.returncode == 7
    assert "[PREVIEW]" in result_text.stdout
    assert "跳过明细:" in result_text.stdout
    assert "冲突明细:" in result_text.stdout
    assert "- A_OVERWRITE01_0001.jpg" in result_text.stdout
    assert "! A_OVERWRITE01_0002.jpg" in result_text.stdout
    assert "提示:" in result_text.stdout
    assert "--allow-overwrite" in result_text.stdout


def test_import_list_dry_run_preserves_state_across_restart():
    """测试 dry-run 后跨重启（重新加载批次）状态不变"""
    SAMPLE_DIR.mkdir(parents=True, exist_ok=True)

    config_path = SAMPLE_DIR / "config.yaml"
    source_dir = SAMPLE_DIR / "source_cards" / "card_A"
    source_dir.mkdir(parents=True, exist_ok=True)
    create_config(config_path, WORK_DIR, ARCHIVE_DIR, ["A"])

    f1 = create_source_file(source_dir, "IMG_0001.jpg", b"photo1content")
    f2 = create_source_file(source_dir, "IMG_0002.jpg", b"photo2content")
    f3 = create_source_file(source_dir, "IMG_0003.jpg", b"photo3content")
    hash1 = get_file_hash(f1)
    hash2 = get_file_hash(f2)
    hash3 = get_file_hash(f3)

    manifest_dir = SAMPLE_DIR / "delivery_list"
    manifest_dir.mkdir(parents=True, exist_ok=True)

    manifest1 = manifest_dir / "m1.csv"
    with open(manifest1, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_STATE001_0001.jpg,{hash1},A,1\n")

    manifest2 = manifest_dir / "m2.csv"
    with open(manifest2, "w", encoding="utf-8") as f:
        f.write("target_name,expected_hash,camera,sequence\n")
        f.write(f"A_STATE001_0002.jpg,{hash2},A,2\n")
        f.write(f"A_STATE001_0003.jpg,{hash3},A,3\n")

    result = run_photo_archive([
        "-c", str(config_path),
        "scan", str(source_dir),
        "--batch-name", "state-test",
        "--json",
    ])
    assert result.returncode == 0
    batch_id = json.loads(result.stdout)["batch_id"]

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest1),
        "--batch-id", batch_id,
        "--json",
    ])
    assert result.returncode == 0

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    state_before = json.loads(result.stdout)

    result = run_photo_archive([
        "-c", str(config_path),
        "import-list", str(manifest2),
        "--batch-id", batch_id,
        "--dry-run",
        "--json",
    ])
    assert result.returncode == 0
    dryrun_result = json.loads(result.stdout)
    assert dryrun_result["added_count"] == 2
    assert dryrun_result["total_delivery_items"] == 1

    result = run_photo_archive([
        "-c", str(config_path),
        "list-batches",
        "--json",
    ])
    batches_after_dryrun = json.loads(result.stdout)
    assert len(batches_after_dryrun) == 1
    assert batches_after_dryrun[0]["delivery_count"] == 1

    result = run_photo_archive([
        "-c", str(config_path),
        "report",
        "--batch-id", batch_id,
        "--json",
    ])
    state_after = json.loads(result.stdout)
    assert state_before["summary"]["total_delivery_items"] == state_after["summary"]["total_delivery_items"]
    assert state_before["summary"]["total_imports"] == state_after["summary"]["total_imports"]
    assert len(state_before["import_records"]) == len(state_after["import_records"])
    assert state_before["import_records"] == state_after["import_records"]

