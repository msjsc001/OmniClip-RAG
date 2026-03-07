# 无界 RAG / OmniClip RAG

[English README](README.md)

**无界 RAG** 是 `OmniClip RAG` 的中文名，寓意是：**跨越任何笔记软件的边界，无缝对接任何 AI**。

它的核心目标不是把你的笔记库绑死到某一个 AI 产品里，而是把你的本地 Markdown / Logseq 笔记库做成一个独立、可热更新、可监督使用的本地检索层。

你先在本地检索，再把你愿意暴露的上下文手动复制给任意 AI。这样你的笔记仍然属于你自己，而不是属于某个聊天产品。

## 核心定位

无界 RAG 适合这样的工作流：

1. 长期在 Logseq 或任意 Markdown 笔记库里写东西。
2. 用本地检索层持续维护索引。
3. 在需要时，把高质量相关页面、语义路径、片段内容打包出来。
4. 再把这一包上下文贴给任意 AI。

这意味着它天然强调：

- 本地优先
- 高解耦
- 热更新
- 可控暴露面
- 不把整库直接交给 AI

## 当前能力

- 桌面 GUI：配置、预检、模型预热、建库、查询、热监听、分类清理
- 双解析器：普通 Markdown + Logseq Markdown
- Logseq 语义支持：页面属性、块属性、`id:: UUID`、块引用、块嵌入
- 混合检索：`SQLite + FTS5 + 结构打分 + LanceDB`
- 本地向量模型：`BAAI/bge-m3`
- 空间预检查：在真正建库前先估算硬盘占用
- 上下文包导出：给任意 AI 使用

## 使用入口

桌面版：

```powershell
.\scripts\run_gui.ps1
```

打包 EXE：

```powershell
.\scripts\build_exe.ps1
```

CLI 仍然保留，用于调试和自动化：

```powershell
.\scripts\run.ps1 status
.\scripts\run.ps1 query "你的问题"
```

## 首次使用建议

1. 打开桌面界面。
2. 选择笔记库根目录。
3. 确认数据目录。
4. 先跑空间预检。
5. 再做模型预热。
6. 再全量建库。
7. 然后开始查询并复制上下文。

## 数据目录

默认数据目录位于 `%APPDATA%\OmniClip RAG`。
如果当前环境对这个目录没有写权限，程序会自动回退到可写目录，避免直接启动失败。

## 当前版本说明

- 版本：`V0.1.0`
- 主交付形态：桌面 GUI
- 当前稳定主线：`torch + bge-m3`

这一版先把“本地知识检索层 + 桌面交互层”做稳，不急着做成一个庞杂的 AI 平台。

## 相关文档

- [English README](README.md)
- [架构说明](ARCHITECTURE.md)
- [更新日志](CHANGELOG.md)
- [空间预检说明](STORAGE_PRECHECK.md)
