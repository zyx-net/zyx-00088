# Photo Archive - 多命令离线相册归档校验 CLI

帮助摄影工作室把存储卡文件整理到客户交付批次的命令行工具。

## 功能特性

- **初始化样例**：生成测试用的存储卡目录、交付清单和配置文件
- **扫描目录**：扫描存储卡照片，计算哈希、识别机位信息
- **导入交付清单**：导入 CSV 格式的交付清单
- **校验**：检测缺片、重复文件、哈希不一致
- **生成修正计划**：根据校验结果生成文件复制/重命名计划，支持保存为命名批次快照
- **可恢复执行队列**：支持断点续执行，进程重启后可继续未完成的批次
- **应用修正**：执行修正计划，归档文件到交付目录，自动跳过已完成项，检测外部修改冲突
- **冲突保护**：目标文件被外部修改或内容不匹配时给出清楚提示，不静默覆盖
- **撤销**：回滚已应用的修正操作，状态自动正确回退
- **状态查询**：实时查看批次进度、待执行/已完成/冲突/可撤销数量统计
- **导出报告**：生成 JSON/CSV 格式的校验报告

## 快速开始

```bash
# 1. 安装
pip install -e .

# 2. 生成样例数据
photo-archive init-sample ./sample_data

# 3. 使用样例配置
cd ./sample_data

# 4. 扫描存储卡
photo-archive -c config.yaml scan ./source_cards

# 5. 导入交付清单
photo-archive -c config.yaml import-list ./delivery_list/delivery_manifest.csv

# 6. 校验
photo-archive -c config.yaml verify

# 7. 生成修正计划（可选：保存为命名批次快照，支持断点续执行
photo-archive -c config.yaml plan --save "2024-wedding-001" --description "2024年婚礼第一批"

# 8. 查看批次状态
photo-archive -c config.yaml status

# 9. 应用修正（支持断点续执行，自动跳过已完成项）
photo-archive -c config.yaml apply

# 或从指定快照继续执行
photo-archive -c config.yaml apply --from-snapshot <snapshot_id>

# 10. 导出报告
photo-archive -c config.yaml report -o report.json --format json
```

## 可恢复批次工作流

```bash
# 1. 生成计划并保存批次快照
photo-archive -c config.yaml plan --save "batch-001"

# 2. 分批执行（限制每次执行2个）
photo-archive -c config.yaml apply --limit 2

# 3. 查看当前进度
photo-archive -c config.yaml status

# 4. 进程重启后继续执行剩余项（自动跳过已完成的2个）
photo-archive -c config.yaml apply

# 5. 遇到冲突时的处理
# 当目标文件被外部修改时，会给出冲突提示，返回退出码9，不会静默覆盖
# 可使用 --skip-conflicts 跳过冲突项继续执行其他项
photo-archive -c config.yaml apply --skip-conflicts
```

## 配置文件 (config.yaml)

```yaml
naming_rule: "{机位}_{批次}_{序号:04d}.{扩展名}"
cameras:
  - A
  - B
  - C
hash_strategy: sha256
archive_dir: ./archive
work_dir: ./work
```

## 非零退出码说明

| 退出码 | 说明 |
|--------|------|
| **0** | 成功 |
| **1** | 通用错误（命令执行失败） |
| **2** | 清单错误（损坏或包含重复文件名） |
| **3** | 哈希不一致（扫描后文件被篡改或期望哈希不匹配） |
| **4** | 重复文件名（源目录或清单中存在重复） |
| **5** | 缺片（清单中存在但源目录中缺失的文件） |
| **6** | 没有可撤销的操作 |
| **7** | 合并冲突（多清单导入存在冲突，需要人工解决） |
| **8** | 批次名冲突（归一化后与现有批次冲突，需要人工处理） |
| **9** | 执行冲突（目标文件被外部修改或内容不匹配，不会静默覆盖） |

也可以通过命令查看：
```bash
photo-archive help-exit-codes
```

## 命令列表

| 命令 | 说明 |
|------|------|
| `init-sample` | 初始化样例数据 |
| `scan` | 扫描源目录中的照片文件 |
| `import-list` | 导入交付清单 (CSV 格式) |
| `verify` | 校验交付清单与源文件的匹配情况 |
| `plan` | 生成修正计划，支持 `--save <名称>` 保存为命名批次快照 |
| `apply` | 应用修正计划，支持 `--from-snapshot <快照ID>` 断点续执行 |
| `status` | 显示批次执行状态和进度统计（待执行、已完成、冲突、可撤销数量） |
| `undo` | 撤销已应用的修正，状态自动回退 |
| `report` | 导出校验报告 |
| `list-batches` | 列出所有批次 |
| `profile save` | 保存当前运行配置为命名 Profile |
| `profile load` | 查看 Profile 详细配置 |
| `profile list` | 列出所有保存的 Profile |
| `profile delete` | 删除指定 Profile |
| `profile export` | 导出 Profile 到 JSON 文件 |
| `profile import` | 从 JSON 文件导入 Profile |
| `profile audit-log` | 查看 Profile 操作审计日志 |
| `help-exit-codes` | 显示非零退出码说明 |

### 新增命令选项说明

#### plan 命令
- `--save <名称>`: 保存为命名批次快照，用于后续断点续执行
- `--description <描述>`: 批次快照的描述信息
- `--profile <名称>`: 使用保存的 Profile 配置

#### apply 命令
- `--from-snapshot <快照ID>`: 从指定的快照继续执行
- `--resume/--no-resume`: 是否跳过已完成的项继续执行（默认跳过）
- `--skip-conflicts`: 跳过存在冲突的项，继续执行其他项
- `--limit <数量>`: 限制本次应用的修正数量
- `--profile <名称>`: 使用保存的 Profile 配置

#### status 命令
- `--details`: 显示详细的修正项列表
- `--json`: 输出 JSON 格式的完整状态信息
- `--profile <名称>`: 使用保存的 Profile 配置

#### undo 命令
- `--profile <名称>`: 使用保存的 Profile 配置

## Profile 可持久化运行配置

Profile 允许你将归档规则、冲突策略、输出格式和日志级别保存为命名配置，之后在多个命令中重放。

### 常用操作

```bash
# 1. 保存当前配置为 Profile
photo-archive -c config.yaml profile save "studio-standard" \
  --description "工作室标准归档配置" \
  --conflict-strategy fail \
  --resume \
  --output-format json \
  --log-level INFO

# 2. 查看 Profile 详情
photo-archive -c config.yaml profile load "studio-standard"

# 3. 列出所有 Profile
photo-archive -c config.yaml profile list

# 4. 使用 Profile 执行命令
photo-archive -c config.yaml plan --profile "studio-standard" --save "batch-001"
photo-archive -c config.yaml apply --profile "studio-standard"
photo-archive -c config.yaml status --profile "studio-standard"
photo-archive -c config.yaml undo --profile "studio-standard"

# 5. 命令行参数覆盖 Profile 配置
# Profile 默认 limit=10，命令行指定 --limit 5 会覆盖
photo-archive -c config.yaml apply --profile "studio-standard" --limit 5

# 6. 导出 Profile
photo-archive -c config.yaml profile export "studio-standard" ./profiles/studio-standard.json

# 7. 导入 Profile
photo-archive -c config.yaml profile import ./profiles/studio-standard.json

# 导入时重命名
photo-archive -c config.yaml profile import ./profiles/studio-standard.json --rename "studio-standard-backup"

# 导入时覆盖已有同名 Profile
photo-archive -c config.yaml profile import ./profiles/studio-standard.json --overwrite

# 8. 删除 Profile
photo-archive -c config.yaml profile delete "old-config"

# 9. 查看审计日志
photo-archive -c config.yaml profile audit-log --limit 50
```

### Profile 工作流示例

```bash
# 场景：为不同客户创建不同的归档策略

# 1. 创建婚礼客户专用 Profile
photo-archive -c config.yaml profile save "wedding-client" \
  --description "婚礼客户归档：高优先级，JSON输出" \
  --conflict-strategy fail \
  --output-format json \
  --log-level INFO

# 2. 创建活动客户专用 Profile
photo-archive -c config.yaml profile save "event-client" \
  --description "活动客户归档：自动跳过冲突，批量处理" \
  --conflict-strategy skip \
  --skip-conflicts \
  --default-limit 50 \
  --output-format text

# 3. 在不同批次中使用对应 Profile
photo-archive -c config.yaml scan ./source_cards/wedding_001 --batch-name "WEDDING001"
photo-archive -c config.yaml import-list ./delivery/wedding_001.csv
photo-archive -c config.yaml verify
photo-archive -c config.yaml plan --profile "wedding-client" --save "wedding-001-plan"
photo-archive -c config.yaml apply --profile "wedding-client"

# 4. 导出 Profile 分享给团队成员
photo-archive -c config.yaml profile export "wedding-client" ./team-profiles/wedding.json

# 5. 团队成员导入后使用
photo-archive -c config.yaml profile import ./team-profiles/wedding.json
photo-archive -c config.yaml apply --profile "wedding-client" --batch-id "..."
```

### Profile 包含的配置项

| 配置项 | 说明 |
|--------|------|
| `naming_rule` | 文件命名规则模板 |
| `cameras` | 机位列表 |
| `hash_strategy` | 哈希算法 (sha256, md5 等) |
| `archive_dir` | 归档目录路径 |
| `work_dir` | 工作目录路径 |
| `conflict_strategy` | 冲突处理策略 (fail, skip, overwrite) |
| `resume` | 是否自动跳过已完成项 |
| `skip_conflicts` | 是否自动跳过冲突项 |
| `output_format` | 默认输出格式 (text, json) |
| `log_level` | 日志级别 (DEBUG, INFO, WARNING, ERROR) |
| `default_limit` | 默认执行数量限制 |

### 非零退出码新增

| 退出码 | 说明 |
|--------|------|
| **10** | Profile 名称冲突（导入或保存同名 Profile 且未使用 --overwrite） |

## 报告内容

报告包含以下部分：
- **文件映射**：原路径 → 目标文件名
- **缺片列表**：清单中存在但源目录中缺失的文件
- **重复文件**：源目录中存在多个同名文件
- **哈希变化**：实际哈希与期望不一致的文件
- **实际执行的修正**：所有已应用的操作记录

## 项目结构

```
photo_archive/
├── cli.py              # CLI 主入口
├── config.py           # 配置管理
├── models.py           # 数据模型
├── storage.py          # 批次历史持久化
├── scanner.py          # 文件扫描和哈希计算
└── commands/
    ├── init_sample.py  # 初始化样例
    ├── scan.py         # 扫描目录
    ├── import_list.py  # 导入交付清单
    ├── verify.py       # 校验
    ├── plan.py         # 生成修正计划
    ├── apply.py        # 应用修正
    ├── undo.py         # 撤销
    └── report.py       # 导出报告
```

## 修复的关键漏洞

### 1. 命名规则字段匹配

**问题**：源卡原始文件名（如 `A_0001.jpg`）和交付清单目标名（如 `A_WEDDING_0001.jpg`）不一致时，会误报为缺片。

**修复**：配置中的 `naming_rule` 参与扫描结果和清单的匹配，通过解析文件名中的机位、序号、扩展名等字段进行智能匹配。

### 2. Apply 前哈希重校验

**问题**：扫描后文件被篡改，apply 时仍会把脏文件归档成正常批次。

**修复**：apply 执行前重新校验源文件当前哈希；如果扫描后文件被改写，阻止复制、返回退出码 3，并在报告中记录 `hash_mismatch`。
