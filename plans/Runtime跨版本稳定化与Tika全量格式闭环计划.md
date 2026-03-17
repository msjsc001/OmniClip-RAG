# Runtime 跨版本稳定化与 Tika 全量格式闭环计划

## 背景
`0.3.1` 暴露出两个交付级问题：

1. `runtime` 仍然默认绑定到 EXE 同级目录，导致跨版本升级时只要目录变化，旧组件注册表里的绝对路径就容易失效；用户把 `v0.3.0/runtime` 挪到 `v0.3.1/runtime` 后，程序会误判大量缺失。
2. `选择 Tika 格式` 虽然已经有“全量目录”的部分逻辑，但仍依赖本机先装好 `tika-server-standard-*.jar` 才能真正显示大目录；没有 JAR 时仍会回退成旧的精简列表。

这两个问题都违背了原始设计目标：

- `runtime` 应该是跨版本复用、渐进修复，而不是每次升级都像重装。
- Tika 格式目录应当先天可见，再决定用户是否安装运行时，而不是“先安装，后看见”。

## 目标
一次修正两条主链：

1. 发布版默认 runtime 安装 / 修复目标改为 `%APPDATA%\\OmniClip RAG\\shared\\runtime`。
2. 保留对 legacy runtime 的自动兼容读取，不强制大文件迁移。
3. `_runtime_components.json` 继续兼容旧绝对路径，同时新安装写相对路径。
4. Tika 选择窗在“无 JAR”条件下也能显示全量目录。
5. 新 build 中继续不打入 runtime、不打入 Tika JAR/JRE。

## 实施方案
### Runtime
- `vector_index.py`
  - `_preferred_runtime_dir_path()` 改为始终优先指向共享 AppData runtime，除非显式设置 `OMNICLIP_RUNTIME_ROOT`。
  - `_legacy_runtime_candidate_dirs()` 扩展为：
    - 当前 EXE 同级 `runtime`
    - 父目录旧 flat `runtime`
    - 同级版本目录 `OmniClipRAG-v*/runtime`
  - `_discover_active_runtime_dir()` 改成“三段式选择”：
    - 先选完整 runtime
    - 再选已有内容的 runtime
    - 最后选仅存在目录的 runtime
  - `runtime_guidance_context()` 补齐：
    - `active_runtime_dir`
    - `preferred_runtime_dir`
    - `install_target_dir`
- `runtime_layout.py`
  - 旧绝对路径注册表失效时，按 basename 回捞当前 `components/` 下的目录。
  - 如果 canonical 目录不存在，则自动扫描 `components/<component>-*` 选择最新版本。
  - `gpu-acceleration` 继续归一化到 `semantic-core`。
- `scripts/install_runtime.ps1`
  - 发布版默认不再写入 `EXE/runtime`，而是写入共享 AppData runtime。
  - 新注册信息使用相对路径，避免跨版本搬目录即失效。

### Runtime UI
- `config_workspace.py`
  - Runtime 页新增两行：
    - 当前使用中的 runtime
    - 默认安装 / 修复目标
  - 手动修复弹窗展示与命令的目标目录改为共享安装目标，而不是当前 active runtime。
- `ui_i18n.py`
  - 新增 active / preferred runtime 文案键值。

### Tika
- `extensions/tika_catalog.py`
  - 格式目录来源改为三层回退：
    1. 已安装 JAR 中的 `tika-mimetypes.xml`
    2. 程序内置 `resources/tika_suffixes_3.2.3.txt`
    3. curated 默认格式
  - `pdf` 永久排除。
- `OmniClipRAG.spec`
  - 固定打入 `resources/tika_suffixes_3.2.3.txt`。
- 现有 Tika UI / registry / service 的全量目录逻辑继续沿用，使 `UNTESTED` / `POOR` / 复合后缀扫描能真正落到建库链路。

## 当前状态
### Phase 1：Runtime 根目录与 legacy 兼容
状态：已完成

已落地：
- 共享 AppData runtime 成为默认安装目标。
- frozen build 不再把 `EXE/runtime` 当成唯一主根。
- legacy runtime 可自动复用。
- 版本化 `components/<component>-时间戳` 能自动发现最新目录。

### Phase 2：Runtime UI 与安装脚本收口
状态：已完成

已落地：
- Runtime 页同时显示 active runtime 与 preferred install target。
- 手动修复命令默认指向共享安装目标。
- PowerShell 安装脚本默认写入共享 AppData runtime。

### Phase 3：Tika 无 JAR 全量目录闭环
状态：已完成

已落地：
- 无 JAR 时也能从内置后缀清单生成大目录。
- PyInstaller bundle 已包含 `tika_suffixes_3.2.3.txt`。
- `pdf` 不出现在 Tika 选择窗。

### Phase 4：验证与构建
状态：已完成

结果：
- `python -m pytest`：`232 passed`
- `python build.py`：成功
- 产物：
  - `dist/OmniClipRAG-v0.3.2/`
  - `dist/OmniClipRAG-v0.3.2-win64.zip`
- 构建核查：
  - `_internal/resources/tika_suffixes_3.2.3.txt` 已进包
  - 发行目录根下无 `runtime/`
  - runtime / Tika JAR / JRE 未被错误打包进 EXE

## 关键文件
- [vector_index.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/vector_index.py)
- [runtime_layout.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/runtime_layout.py)
- [config_workspace.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/ui_next_qt/config_workspace.py)
- [tika_catalog.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/extensions/tika_catalog.py)
- [install_runtime.ps1](/D:/软件编写/OmniClip%20RAG/scripts/install_runtime.ps1)
- [OmniClipRAG.spec](/D:/软件编写/OmniClip%20RAG/OmniClipRAG.spec)

## 后续建议
- 下一阶段优先做实机点验：
  - 旧版 `runtime` 不重下直接被 `0.3.2` 复用
  - 无 Tika JAR 时选择窗直接显示大目录
  - 下载新组件后自动写入共享 runtime，后续版本继续复用
- 如果未来需要更强的跨版本升级体验，可以再增加“检测到 legacy runtime 可迁移到共享目录”的显式引导，但不应在首启时自动复制数 GB 数据。
