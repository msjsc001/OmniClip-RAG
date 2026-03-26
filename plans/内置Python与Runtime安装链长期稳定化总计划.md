# 内置 Python 与 Runtime 安装链长期稳定化总计划

## 文档定位

这份计划的目标不是修一个零碎安装报错，而是把 OmniClip RAG 现有的 Windows Runtime 安装链从“依赖用户系统 Python 的开发者式流程”，收口为“普通 Windows 用户下载软件后，只在软件内下载 Runtime 就能用”的产品级流程。

本文必须满足 4 个用途：

1. 当前聊天窗中断、废弃、损坏后，后续任意聊天窗或 AI 能直接接手；
2. 实施者不需要重新梳理“为什么现在用户还会卡在 Python / pip / 源访问”；
3. 实施者不需要再做关键决策，本文已经把路线、接口、边界、非目标、验收标准全部锁死；
4. 后续真实开始编码时，可直接把本文当成实施蓝图、验收标准和回归约束使用。

## 当前背景与问题定义

### 1. 当前产品真实结构

OmniClip RAG 当前已经采用“轻量主程序 EXE + 外置 Runtime sidecar”架构：

- 主程序 EXE 不直接打包 `torch`、`sentence-transformers`、`scipy`、`lancedb`、`onnxruntime`、`pyarrow` 这类重依赖；
- Runtime 通过 `InstallRuntime.ps1` 后装到共享 Runtime 目录；
- 这条链路本身是正确的，问题不在“Runtime 外置”，而在安装器仍依赖用户系统 Python。

### 2. 已查实的旧问题

旧安装脚本真实行为存在这些结构性问题：

1. 用户机器必须先有合适 Python；
2. 脚本对 Python 版本 / launcher 的识别与文档口径不完全一致；
3. 运行时依赖仍由 pip 在现场自由解析与下载；
4. 失败时用户看不到真正根因，只看到总错。

### 3. 本计划的总体目标

在**不做 Runtime 预构建大包**的前提下，把 Runtime 安装链优化到这种产品状态：

- 新机器上没有 Python，也能在软件内完成 Runtime 下载与安装；
- 用户系统已有 Python，不会干扰软件 Runtime 安装主链；
- 安装链可以做到半年到一年维护一次仍然比较稳；
- 普通 Windows 10/11 x64 用户在主流网络环境下，基本只需“下载软件 -> 在 Runtime 页安装 -> 用”，不需要软件外额外配置。

## 本次改造的最终目标

### 目标 1：彻底去掉用户系统 Python 的前置要求

- Runtime 安装主链必须只依赖软件自带的 Python 3.13 运行时；
- 用户机器上即使完全没有 Python，也能完成 Runtime 安装；
- 系统 Python 存在时，也不能污染主链。

### 目标 2：让 Runtime 安装不再依赖现场自由依赖解析

- 不能继续让 pip 在现场自由解析整套依赖树；
- 必须改成：按应用方锁定的 wheel manifest 下载与安装。

### 目标 3：下载与安装分离

- 先下载 wheel 到本地 wheelhouse/staging；
- 校验通过后再从本地 wheelhouse 执行安装；
- 安装阶段不再依赖外网。

### 目标 4：失败必须可诊断

- 用户和开发者都能知道：
  - 哪个阶段失败；
  - 哪个 wheel 失败；
  - 是下载失败、校验失败、安装失败还是验证失败；
- 不再只有一句 `Runtime installation failed.`

### 目标 5：无 N 卡机器上的 CUDA 组件默认非必需，但允许手动测试安装链

- 没有 NVIDIA GPU 的机器上，CUDA Runtime 继续属于“默认不需要”的组件；
- 但 UI 不能再把它做成“不可下载 / 不可修复”；
- 用户必须可以手动下载或修复这类组件，用来测试下载 / 安装链是否正常；
- 下载成功后状态必须是“已安装（当前机器未验证）”之类的中性态，而不是误报成 `CUDA ready`。

### 目标 6：保持当前外置 Runtime sidecar 架构

- 不把完整 Runtime 打进主 EXE；
- 不改 Markdown / 扩展 / MCP 主功能；
- 不把这次改造成“Runtime 预构建大包方案”。

## 最终决策（已锁定）

### 决策 1：采用“内置 Python + 锁定 manifest + 分阶段安装”

这是本计划的唯一推荐路线，不再在实施时重新做路线选择。

### 决策 2：不做 Runtime 预构建大包

本轮明确不做：

- CPU Runtime 全量压缩包分发；
- CUDA Runtime 全量压缩包分发；
- Runtime 资产的 GitHub Release 独立大包化。

### 决策 3：系统 Python 不参与正式用户主链

正式产品链必须：

- 优先使用内置 Python；
- 默认不走系统 Python fallback。

### 决策 4：锁定“已验证 manifest”，而不是自由范围依赖

- 不再只写抽象依赖范围；
- 通过 manifest 明确 profile / component / 依赖集 / 来源策略 / 校验与验证要求。

### 决策 5：CPU 先做稳，CUDA 沿用同一安装框架

- 本轮先把 CPU Runtime 的稳定性做到位；
- CUDA 继续共用同一安装框架，但不把 CUDA 问题扩大成额外架构战线；
- 无 N 卡机器上的 CUDA 组件语义固定为“非必需但可测试”。

## 实施方案

## 一、发布与构建链

### 1. 新增内置 Python 资产

在 GUI 发布物中引入一份内置 Python 3.13 Windows x64 运行时，作为 Runtime 安装专用环境。

要求：

- 只服务于 Runtime 下载 / 安装 / 验证链；
- 不替代主程序 EXE 运行环境；
- 不把它误当成应用数据或 Runtime sidecar 本体；
- 必须随 GUI 发布物一起分发。

建议目录语义固定为：

- `<app_dir>/runtime_support/python/`

### 2. build.py 调整

构建链必须允许 GUI 包新增：

- 内置 Python；
- `InstallRuntime.ps1`；
- `runtime_support/` manifest 与驱动；
- `RUNTIME_SETUP.md`。

同时继续禁止：

- `torch`
- `transformers`
- `lancedb`
- `pyarrow`
- `scipy`
- `onnxruntime`
- `sentence_transformers`
- `numpy`
- `pandas`

这类 Runtime payload 直接混入主包。

### 3. 版本更新策略

内置 Python 固定在一条主版本线上，例如：

- Python `3.13.x`

未来半年到一年维护一次时，优先更新：

- 内置 Python 小版本；
- manifest 依赖清单；
- 校验策略与 SHA 记录。

## 二、Runtime 安装链重构

### 1. 安装器优先级

`InstallRuntime.ps1` 的 Python 选择逻辑固定改为：

1. 先找软件自带 Python；
2. 若不存在或损坏，再明确报“内置 Python 缺失/损坏”；
3. 默认不走系统 Python。

### 2. 两阶段安装模型

Runtime 安装固定拆成两个主要阶段：

#### 阶段 A：下载 wheel

- 按 manifest 逐个下载 wheel 到本地 wheelhouse/staging；
- 每个文件下载后做校验；
- 只有下载 / 校验成功，才允许进入安装阶段。

#### 阶段 B：离线安装

- 使用内置 Python；
- 从本地 wheelhouse 执行：
  - `pip install --no-index --find-links=<wheelhouse>`
- 安装阶段不再走网络。

### 3. wheel manifest

每个 Runtime 组件清单必须固定成 manifest 文件，而不是散落在脚本里手写范围依赖。

manifest 至少包含：

- `profile`
- `component`
- `python_tag`
- `platform_tag`
- `requirements`
- `source_profiles`
- `cleanup_patterns`
- `required_modules`
- `validation_probes`

### 4. 源与回退策略

每个依赖组可以配置多个来源：

- 官方源；
- 国内镜像源；
- 备用源。

回退规则固定为：

- 同一份 manifest 下的依赖集合不可随源变化；
- 只允许下载来源变化，不允许依赖树变化。

### 5. 安装目标目录

继续保留当前共享 Runtime sidecar 目录模型：

- `<active data_root>/shared/runtime`

新安装链不得改变当前“active data_root 决定整个环境”的原则。

### 6. 验证链

安装完成后继续执行模块验证，但增强为结构化诊断：

- 使用内置 Python；
- 在目标 Runtime 环境中验证：
  - `torch`
  - `numpy`
  - `scipy`
  - `sentence_transformers`
  - `transformers`
  - `huggingface_hub`
  - `safetensors`
  - `lancedb`
  - `onnxruntime`
  - `pyarrow`
  - `pandas`
- 必须确认模块解析路径位于目标 Runtime payload 下，而不是系统环境。

## 三、应用内 Runtime UX

### 1. Runtime 页面文案

应用内 Runtime 页面、新手引导、安装说明全部改口径：

- 不再告诉用户“先装 Python”；
- 改成：
  - 软件会优先使用内置 Python 安装 Runtime；
  - Runtime 安装无需用户自行安装 Python；
  - 如果安装失败，请查看诊断日志路径。
- 同时固定一条 GPU 文案语义：
  - 没有 N 卡的机器默认不需要 CUDA Runtime；
  - 但仍然允许用户手动下载 / 修复 CUDA 组件来测试安装链；
  - 下载成功不等于当前机器 GPU 可用。

### 2. 安装阶段提示

UI 必须明确区分：

- 正在下载依赖；
- 正在校验依赖；
- 正在安装依赖；
- 正在验证 Runtime；
- 已完成；
- 失败。

### 3. 错误回显

最终 UI 至少要展示：

- 失败阶段；
- 失败文件 / 包名；
- 诊断日志路径；
- 建议下一步操作。

### 4. 日志与诊断文件

Runtime 安装链必须生成结构化诊断文件，建议落到：

- `<data_root>/shared/logs/runtime/`

至少包含：

- 时间；
- 安装 profile / component；
- 使用的内置 Python 版本；
- 当前 active data root；
- wheel 下载结果；
- 校验结果；
- 安装结果；
- 验证结果；
- 最终错误摘要。

## 四、系统 Python 与环境污染隔离

### 1. 内置 Python 必须隔离运行

调用内置 Python 时必须采用隔离模式，并显式控制：

- `sys.path`
- site-packages
- PATH / DLL 搜索路径

目标是：

- 用户系统 Python；
- 用户 site-packages；
- 用户 pip 配置；
- 用户 PATH

都不能影响正式 Runtime 安装链。

### 2. 系统 Python 的角色

系统 Python 只允许作为：

- 开发调试；
- 紧急诊断。

不允许作为普通用户主链 fallback。

## 五、范围与非目标

### 本轮明确要做

- 内置 Python；
- manifest 化依赖清单；
- 下载 / 安装分离；
- 多源回退；
- 强诊断日志；
- 应用内文案与流程更新。

### 本轮明确不做

- Runtime 预构建大包；
- CPU / CUDA 整包离线分发；
- 将 Runtime payload 直接打进主 EXE；
- 重做 Markdown / 扩展 / MCP 主功能；
- 改动当前 active data root / shared runtime 的整体架构。

## 风险与规避

### 风险 1：包体变大

- GUI 包会增大；
- 但增长量主要来自内置 Python，不是 Runtime payload；
- 这是可接受成本。

### 风险 2：manifest 维护

- 未来半年到一年需要重新验证 manifest；
- 但这比维护 Runtime 大包轻得多；
- 且它是可控维护成本。

### 风险 3：远端下载仍然存在

- 因为本轮不做 Runtime 包，仍然依赖下载 wheel；
- 但风险将被明确收敛到“下载阶段”；
- 不再与“用户系统 Python / 自由依赖解析”混成一锅。

## 测试与验收

### 1. 基础安装场景

- Windows 10 x64 干净环境，无 Python；
- Windows 11 x64 干净环境，无 Python；
- GUI 包启动后，Runtime 页能完成 CPU Runtime 安装；
- 安装完成后可进入本地语义建库与查询链。

### 2. 系统 Python 干扰隔离

- 机器上有 Python 3.14；
- 机器上有多个 Python 和 py launcher；
- Runtime 安装主链仍强制使用内置 Python；
- 结果不受用户系统 Python 影响。

### 2.5 无 N 卡机器 CUDA 安装链可测化

- 没有 NVIDIA GPU 的机器上，GUI 里 CUDA / GPU 组件仍应显示“默认非必需”；
- 但“下载 / 修复”按钮必须可点击；
- 用户手动安装成功后，状态必须是“已安装（当前机器未验证）”之类的中性态；
- 不能误报为 `CUDA ready`；
- “执行验证”必须明确提示“当前机器不具备 GPU 验证条件”，而不是伪造成功或失败。

### 3. 下载失败与诊断

分别模拟：

- 官方源失败；
- 镜像源失败；
- 单 wheel 下载失败；
- 校验失败；
- 安装阶段失败；
- 验证阶段失败。

要求：

- UI 明确指出失败阶段；
- 日志中能定位具体 wheel 或模块；
- 用户能拿诊断文件反馈。

### 4. data_root 一致性

- 切换 `data_root` 后安装 Runtime；
- 必须只写到 active `data_root` 对应的 shared runtime；
- 不得偷偷写回默认 `%APPDATA%` 环境。

### 5. 不回归

- 不破坏主程序轻量打包策略；
- 不把 Runtime payload 混入 EXE 包；
- 不破坏现有 Runtime sidecar 复用逻辑；
- 不破坏 Markdown / 扩展 / MCP 主线。

### 6. 流量约束下的自检方式

- 开发与回归测试中禁止依赖超过 200MB 的真实大模型或 Runtime 下载；
- 所有安装链回归优先使用：
  - 本地伪造 / 微型 wheelhouse；
  - manifest fixture；
  - 下载与校验 mock；
  - 小体积测试 wheel；
- EXE 自检允许构建产物，但不允许在验证阶段临时下载巨量模型或 Runtime 资产。

## 假设与默认

- 固定主版本线：内置 Python 使用 `3.13.x`
- 正式用户链默认不 fallback 到系统 Python
- 仍然保持“轻量 GUI + 外置 Runtime sidecar”而不是 Runtime 包
- 多源只是下载回退，不是依赖解析回退
- 推荐维护节奏：半年到一年更新一次内置 Python 小版本和 wheel manifest

## 最终判断

按当前已锁定的范围，这份计划已经足够周全，已经达到可放心进入实施的程度。  
大的该考虑的都已经考虑到了；剩下真正的风险，不在计划本身，而在实施时是否严格遵守本计划的边界与纪律。
