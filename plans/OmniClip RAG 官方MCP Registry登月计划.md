# OmniClip RAG 官方MCP Registry登月计划

## 背景

`modelcontextprotocol/servers` 已不再接受把第三方服务器链接加到 README 的收录方式，官方主发现路径已经切到 MCP Registry。

因此，OmniClip RAG 的 MCP 对外发布主线从“README 挂名”转为“官方 Registry 标准发布”。

## 核心决策

- Registry 名称固定为：`io.github.msjsc001/omniclip-rag-mcp`
- 标题固定为：`OmniClip RAG`
- 主描述固定为：
  - `Read-only local-first MCP server for private Markdown, PDF, and Tika-backed search on Windows.`
- 首次 Registry 版本固定为：`0.4.1`
- 包型固定为：`MCPB`
- transport 固定为：`stdio`
- 首次发布固定为：**手动发布**

## 为什么不用 0.4.0

Registry 版本一旦发布，元数据即不可直接修改。`0.4.0` 已经作为 GUI/MCP 双壳正式版本公开，因此不再拿它承担第一次 Registry 试水风险。

`0.4.1` 被专门保留为第一发 Registry 版本。

## 当前实施范围

### 仓库内已落地

- 新增 MCP Registry 元数据单一事实源：
  - `omniclip_rag/mcp/registry.py`
- 新增正式根级：
  - `server.json`
- 新增 MCPB 打包脚本：
  - `scripts/build_mcpb.ps1`
- 文档更新：
  - `README.md`
  - `README.zh-CN.md`
  - `MCP_SETUP.md`
  - `CHANGELOG.md`
  - `releases/RELEASE_NOTES_v0.4.1.md`

### 打包策略

- 普通用户：
  - `OmniClipRAG-v0.4.1-win64.zip`
  - `OmniClipRAG-MCP-v0.4.1-win64.zip`
- Registry / MCPB 客户端：
  - `omniclip-rag-mcp-win-x64-v0.4.1.mcpb`

## 首次手动发布顺序

1. 先构建 GUI / MCP 包
2. 再构建 `.mcpb`
3. 计算 `.mcpb` 的 `SHA256`
4. 上传 GitHub Release 资产
5. 确保 Release 为**公开状态**，不是 Draft
6. 再用官方 `mcp-publisher` 发布 Registry

## 已记录的关键坑

- **不要用 Draft Release 的 URL 去 publish**
  Why：Registry 服务器会直接抓取 `.mcpb` 链接做校验，Draft 链接对外是 404。

- **MCPB manifest 的入口路径必须精准指向 `OmniClipRAG-MCP.exe`**
  Why：PyInstaller onedir 结构体积大、层级多，路径一旦写偏，客户端解包后会找不到启动入口。

- **Windows 上统一优先使用 `npx` 调 MCPB CLI**
  Why：这样可以绕开 PowerShell 对 npm 全局脚本的默认执行策略拦截。

- **不要假设 `mcp-publisher` 一定存在同名 npm 包**
  Why：当前官方 Registry quickstart 更像把它当官方发布工具链，而不是 npm 包名；发布时应按官方最新 quickstart 为准。

## 后续演进

- 第一次手动 Registry 发布跑通后，再考虑 GitHub Actions / OIDC 自动化
- `Streamable HTTP` 是后续 transport 线，不属于本次 Registry 登月计划
