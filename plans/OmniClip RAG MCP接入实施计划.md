# OmniClip RAG MCP 接入实施计划

## 背景

OmniClip RAG 当前已经具备较成熟的本地检索内核：

- Markdown / Logseq 主链
- PDF 独立链
- Tika 扩展格式链
- 混合检索与统一来源标签
- 独立 Runtime 管理
- 共享 AppData 数据与 Runtime 根

因此，MCP 这条线不应重新发明后端，而应该把现有检索内核暴露成一个**标准、只读、无头**的接口层。

## 核心目标

让任意支持 MCP 的 AI 客户端，都能通过标准 `stdio` transport 调用 OmniClip 的本地知识检索能力。

V1 产品形态固定为：

- `OmniClipRAG.exe`
  - 继续做 GUI
  - windowed
- `OmniClipRAG-MCP.exe`
  - 只做 headless MCP server
  - console / stdio

## 红线

- MCP 第一版严格只读
- 不暴露建库、删库、改配置、写笔记
- 不复用 GUI windowed EXE 承担 stdio server
- 不让 MCP 变成第二套后端
- 不允许静默降级
- 不允许把日志写进 stdout

## 架构决策

### 1. 双壳同核

MCP 不是 GUI 的附属参数，而是第二个壳：

- 共享：bootstrap、RuntimeContext、DataPaths、QueryService、结果格式化
- 分离：启动方式、打包子系统、UI 依赖链

### 2. tools-only

V1 仅提供两个工具：

- `omniclip.status`
- `omniclip.search`

不引入 resources、prompts 或写能力。

### 3. stdio-first

V1 先做本地 `stdio` MCP server，同时在代码分层上预留未来 `Streamable HTTP` 的接口位置。

### 4. snapshot-read

每次 MCP 查询都固定 live snapshot：

- config snapshot
- runtime snapshot
- index generation / snapshot id

即使 GUI 正在建库，MCP 仍只读已稳定的一代，不读半成品。

### 5. 显式降级

若语义 Runtime 或本地模型不可用：

- 允许退回 `lexical_only`
- 但必须通过 `effective_mode / degraded / warnings` 公开说明

## 阶段状态

### Phase 1：共享无头启动层

状态：已完成

已落地：

- `omniclip_rag/headless/bootstrap.py`
- GUI 与 MCP 共用 Runtime / DataPaths / QueryService 初始化
- `launcher_support.py` 提炼出共享 bootloader 逻辑

### Phase 2：MCP core 与 stdio 入口

状态：已完成

已落地：

- `omniclip_rag/mcp/core.py`
- `omniclip_rag/app_entry/mcp.py`
- 独立 `launcher_mcp.py`
- 两个只读工具：
  - `omniclip.status`
  - `omniclip.search`

### Phase 3：读写安全与只读行为

状态：已完成

已落地：

- MCP 查询使用现有 QueryService 主链
- `service.query(..., export_result=False)`，避免 MCP 产生导出副作用
- SQLite 加入 `busy_timeout`
- 查询按 live snapshot 只读当前稳定代

### Phase 4：文档与接入样例

状态：已完成

已落地：

- `MCP_SETUP.md`
- Claude / Cursor / Cline 示例配置
- README / README.zh-CN / ARCHITECTURE 补充 MCP 路线

### Phase 5：双构建验证

状态：进行中

目标：

- GUI 包继续保持 windowed
- MCP 包单独生成 `OmniClipRAG-MCP.exe`
- 两个包都不混入 runtime payload、模型缓存、Tika JAR/JRE、用户数据

## 当前交付边界

本阶段 V1 明确只覆盖：

- stdio
- tools-only
- 单机本地使用
- 只读搜索
- 显式降级

## V1.5 / V2 预留

- Streamable HTTP transport
- 远程 connector / ChatGPT developer mode 适配
- 更丰富的 status 字段
- 可选 resources 暴露

## 验收标准

- MCP 入口启动时不导入 Qt
- `omniclip.status` 与 `omniclip.search` schema 稳定
- search 同时返回 `structuredContent + content`
- 每条结果带 `source_label`
- Runtime 缺失时显式降级而非静默假装正常
- GUI 运行中，MCP 查询不出现锁库崩溃
- 可打出独立 `OmniClipRAG-MCP.exe`
