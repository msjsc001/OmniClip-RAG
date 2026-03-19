# OmniClip 官网设计审稿提问包

## 项目背景

OmniClip RAG / 方寸引 是一个：

- Windows 本地知识检索桌面应用，
- 只读 MCP Server，
- local-first 私有知识检索工具，
- 面向 Markdown / PDF / Tika 扩展格式，
- 已上架官方 MCP Registry，
- 已有 GitHub Releases 与 MCPB 发布线。

它不是聊天壳，而是一个“本地知识路由器 / 上下文分发器”。

## 当前网站目标

正在为当前仓库新增一个 GitHub Pages 官网，放在 `docs/` 下。

目标不是做一个花哨营销页，而是做一个：

- 极简、
- 可信、
- 长期可维护、
- 与产品哲学一致、
- 能承接 GitHub / Registry / 社区流量的单页官网。

## 当前已锁定的约束

- 纯 HTML + CSS + 少量原生 JS
- 不使用 React / Vue / Tailwind / npm 构建链
- 单页
- 默认英文，手动切换中文
- 首页先讲 private / local-first / retrieval，再讲 MCP
- 视觉风格是极简漫画 / editorial / 莫兰迪 / 大留白
- 网站里必须同时出现：
  - 1 张主视觉
  - 2 张真实截图
  - 1 张 MCP 概念图

## 现有首页结构

1. Hero
2. TL;DR
3. 三张能力卡
4. Workflow
5. MCP 区
6. 真实截图区
7. Trust 区
8. Footer

## 我最想让你审的点

1. 对 OmniClip 这种项目来说，当前首页首屏是否已经把“private + local-first + retrieval”放在了正确的第一叙事位置？
2. 对技术用户和普通 Windows 用户同时存在的场景，这种“项目官网 + 产品落地页混合体”是否合理？
3. 在“极简漫画 / editorial 风格”和“真实工程信任感”之间，目前最该避免的视觉失衡是什么？
4. 截图区是否应该继续保留 2 张真实截图，还是应该压缩成 1 张截图 + 1 张工作流图？
5. MCP 在首页中的权重是否已经合适，还是仍然过强 / 过弱？
6. 如果目标是 GitHub / Registry / 社区传播后的 10 秒理解，这个网站还缺哪一块关键信息？

## 当前最重要的判断标准

请不要从“好不好看”单独评价，而是从下面 4 个目标来评价：

- 陌生人能否 10 秒看懂
- 是否像长期维护的技术项目官网
- 是否能提高下载 / GitHub / MCP Setup 的转化
- 是否和 OmniClip RAG 的产品哲学一致
