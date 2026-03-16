# GPU Runtime 与扩展建库 UX 收尾计划

## 目标
把当前第二阶段的主问题收束成一条新的唯一战线：

- 让 `Runtime -> N卡 / CUDA 加速（可选）` 不只是“能下载”，而是 **UI 转绿 + 查询台真实用上 GPU + 日志可证明**
- 让 `Runtime` 刷新检测不再卡 UI，而是分层、异步、可解释
- 让 `扩展格式 -> 来源目录` 的建库操作从“只看 percent/current/total”升级成用户能理解、能信任的阶段化流程
- 让新窗口或新 AI 接手时，能直接沿本计划继续，而不是重新围绕 Runtime、GPU、扩展建库 UX 四处散修

## 与上一份计划的关系
- 上一份主计划：[Markdown主查询与Runtime稳定性RCA计划.md](/D:/软件编写/OmniClip%20RAG/plans/Markdown%E4%B8%BB%E6%9F%A5%E8%AF%A2%E4%B8%8ERuntime%E7%A8%B3%E5%AE%9A%E6%80%A7RCA%E8%AE%A1%E5%88%92.md)
- 上一份计划主要解决的是：
  - 构建版 EXE 的 Markdown 主查询到底有没有真正执行高级搜索
  - Runtime 健康探测与真实查询执行是否一致
- 当前新计划是在上一份计划基础上进入的 **新阶段**：
  - CPU 语义主链已明显收口
  - 现在进入 `GPU Runtime 真实生效` 与 `扩展建库 UX 收尾` 阶段

## 当前唯一主问题
### 问题 A：GPU Runtime 行不是“真组件”，而是派生能力灯
现象：
- 用户在 `Runtime` 页面下载 `N卡 / CUDA 加速（可选）`
- 下载终端可能显示成功
- 但 UI 仍可能提示“需要修复”
- 用户无法确定：
  - GPU 组件是否真的进了 live runtime
  - 查询台是否真的用上 GPU

当前代码事实：
- `gpu-acceleration` 行目前不是独立真实组件，而是 **派生能力行**
- 安装目标会映射到 `semantic-core + cuda`
- 就绪判定还额外依赖 `detect_acceleration().cuda_available`
- 这导致：
  - 下载状态
  - 探测状态
  - 执行验证状态
  被混成了一个布尔灯

### 问题 B：GPU 能力健康不等于本次查询真的执行了 CUDA
现象：
- UI 可能显示 GPU 存在、CUDA 可用、Runtime 某些部分已就绪
- 但用户真正关心的是：
  - `查询台` 本次 query 的 embedding 是否真的跑在 CUDA
  - reranker 是否真的跑在 CUDA
  - 是否只是 capability 层看起来“可用”

当前判断：
- 这和上一阶段遇到的 `capability healthy != actual query executed` 是同一类问题
- 只是对象从 CPU 语义主链换成了 CUDA 语义主链

### 问题 C：Runtime 刷新检测是重型探针，容易卡 UI
现象：
- Runtime 页下载后点击“刷新检测”，用户感知上会卡
- 刷新过程中用户无法判断：
  - 是程序挂住了
  - 还是正在跑重探测

当前判断：
- 当前刷新链没有明确分成：
  - 轻探测
  - 中探测
  - 执行验证
- 也没有把重探测稳定地下放到后台 worker

### 问题 D：扩展来源目录建库 UX 仍偏数值化，不够阶段化
现象：
- `扩展格式 -> 来源目录 -> 建库`
- 当前更多展示的是：
  - `percent/current/total`
- 用户更关心的是：
  - 现在在做哪一步
  - 是否真的在工作
  - 能不能安全关闭
  - 对已建成目录，再点建库时究竟是“更新”还是“重建”

当前判断：
- 现在行级状态机不够清晰
- 行级“已建成 -> 再点建库”缺少明确确认与二选一交互

## 当前已经确认的事实
### 已确认 1：详细查询排错日志不会无限膨胀
当前实现：
- `query_trace_logging_enabled` 只是控制是否额外把 trace 打入普通活动日志
- 普通活动日志使用的是 `RotatingFileHandler(maxBytes=..., backupCount=3)`

结论：
- `配置 -> 数据 -> 把详细查询排错日志写入活动日志`
- **会受同一套单个日志文件上限与日志轮转限制**
- 不会无限增长成单一超大日志文件

关键位置：
- [config.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/config.py)
- [service.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/service.py)
- [app_logging.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/app_logging.py)

### 已确认 2：当前 GPU 行确实是派生状态，不是真组件
关键位置：
- [config_workspace.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/ui_next_qt/config_workspace.py)

当前关键事实：
- `gpu-acceleration` 安装目标映射到 `semantic-core + cuda`
- `gpu-acceleration` 的 `ready` 还依赖：
  - `runtime_component_status('semantic-core')`
  - `detect_acceleration().cuda_available`

结论：
- 当前 GPU 行的组件模型、探测模型、展示模型没有完全对齐

### 已确认 3：扩展来源目录当前进度提示确实偏数值化
关键位置：
- [extensions/service.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/extensions/service.py)
- [config_workspace.py](/D:/软件编写/OmniClip%20RAG/omniclip_rag/ui_next_qt/config_workspace.py)

当前关键事实：
- 行级进度主要依赖：
  - `overall_percent`
  - `current`
  - `total`
  - `current_path`
- 但用户侧主感知仍不足以判断：
  - 当前阶段
  - 是否安全关闭
  - 是更新还是重建

## 当前禁止事项
为了防止再次散乱推进，从本计划开始，以下行为视为禁止：

- 不允许继续把 GPU Runtime 行当成一个普通组件按钮问题零碎修补
- 不允许再以 `torch.cuda.is_available()` 单独作为“GPU 就绪”的完成标准
- 不允许先修 Runtime 页文案与颜色，而不修真实执行验证链
- 不允许在没有端到端 GPU 证据前，宣称“查询台已用上 GPU”
- 不允许继续只给扩展来源目录显示 percent/current/total 就当作建库 UX 完成
- 不允许一边修 GPU Runtime、一边再去散修 PDF/Tika 质量、启动动画、无关样式
- 不允许靠大量真实下载反复试错；优先做零下载 canary 和模拟验证

## 当前最可能的根因排序
1. `gpu-acceleration` 行本身是“假组件”，导致下载成功和 ready 变绿之间没有稳定映射
2. GPU ready 目前停留在 capability 层，没有执行验证层
3. QueryService / QueryWorker 没有把 `planned device / resolved device / actual execution / fallback reason` 钉死到结构化日志
4. Runtime 刷新检测没有分层，重探测跑在 UI 触发链，造成卡顿或假死感
5. 扩展来源目录建库状态模型太“数值化”，用户无法形成清晰心智

## 高级 AI 审核后的新增硬约束
### 必须新增的漂移指纹
后续所有 GPU Runtime / Query 执行日志都必须追加以下字段，禁止再靠猜测判断当前到底使用了哪一套运行时或索引：

- `build_id`
- `exe_version`
- `runtime_manifest_version`
- `live_runtime_id`
- `pending_runtime_id`
- `index_generation_id`
- `workspace_id`
- `index_built_for_workspace`
- `index_built_at`

### GPU 查询链必须新增的证据字段
不能只记录“CUDA 可用”，还必须能回答“这次 query 为什么没有真的在 GPU 上执行”：

- `device_policy`
- `planned_device`
- `resolved_device`
- `embedder_requested_device`
- `embedder_actual_device`
- `reranker_requested_device`
- `reranker_actual_device`
- `vector_query_planned`
- `vector_query_executed`
- `vector_exception_class`
- `vector_exception_message`
- `stage_fallback_reason`
- `postfilter_drop_count`
- `stage_ms.lexical`
- `stage_ms.vector`
- `stage_ms.fusion`
- `stage_ms.rerank`
- `stage_ms.finalize`

### 四模式对照升级为硬要求
本阶段不再只做三模式；所有 GPU / Query 主链诊断必须支持以下四种模式，而且必须复用同一条 QueryService 主链：

- `lexical-only`
- `vector-only`
- `hybrid_no_rerank`
- `hybrid`

### 明确的禁止事项补充
- 禁止在 QueryWorker 外另写一套 `vector-only` / `gpu-only` helper 来证明主链没问题
- 禁止用“UI 绿了”或“torch.cuda.is_available() == true”代替“查询台真实用上 GPU”
- 禁止只凭一次成功就宣布收官；至少需要冷启动和固定 query 多次重复验证

## 当前唯一验收对象
### GPU Runtime 真实完成标准
必须同时满足：

1. live runtime manifest 中所需组件齐全
2. 基础 CUDA 烟雾测试通过
3. 至少有一次 **同主链** GPU 执行验证通过
4. 最近一次执行验证绑定当前 `runtime_instance_id`
5. 如果退回 CPU，必须记录 `fallback_reason`

附加要求：
- UI 上的 `gpu-acceleration` 不再只靠单一 `ready bool`
- 至少要能区分：
  - `install_state`
  - `probe_state`
  - `execution_state`
  - `reason`
- 能力行颜色语义固定为：
  - 灰：缺组件 / 不需要
  - 黄：已安装且探测通过，但尚未做执行验证
  - 绿：执行验证通过
  - 红：探测失败或最近一次执行验证失败

### 查询台真实 GPU 生效标准
必须同时满足：

1. `device_policy=require_cuda` 的查询计划成立
2. `resolved_device` 为 `cuda`
3. embedder 实际执行设备为 `cuda`
4. reranker 启用时，reranker 实际执行设备也为 `cuda`
5. 日志存在可证明字段，而不是只出现“CUDA 可用”

附加要求：
- `vector-only` 必须是 **pure vector**，不能经过 lexical seed
- `hybrid_no_rerank` 必须保留，用来把 reranker 问题和 fusion/post-filter 问题切开

### 扩展来源目录建库 UX 完成标准
必须同时满足：

1. 每行来源目录都有独立阶段状态
2. 已建成来源再次点击建库时，必须先明确选择：
   - 扫描更新
   - 重建
3. 进度不只显示数字，还要显示：
   - 当前阶段
   - 当前文件
   - 已处理/跳过/错误数
   - 是否建议关闭软件
4. 用户能判断：
   - 当前是否真的在工作
   - 当前是否适合关闭软件

## 当前最佳推进顺序
### Phase 0：冻结范围与审计基线
**状态：已完成**

目标：
- 把当前主问题冻结为：
  - GPU Runtime 真实生效
  - 扩展来源目录建库 UX
- 不再散修外围

本阶段已完成事实：
- 已确认查询排错日志受日志轮转限制
- 已确认 GPU 行是派生能力行
- 已确认扩展来源目录当前进度提示偏数值化

### Phase 1：重建状态模型与可证明日志
**状态：已完成**

目标：
- 把 `GPU Runtime` 从单一 `ready bool` 改为三层：
  - 组件安装状态
  - 探测状态
  - 执行验证状态
- 把查询设备日志从抽象文案升级成结构化证据

必须落地的字段：

#### `GPU_CAPABILITY_PLAN`
- runtime_instance_id
- build_id / exe_version
- workspace_id
- device_policy
- planned_device
- embedding_model_id
- reranker_model_id
- reranker_enabled

#### `GPU_CAPABILITY_PROBE`
- install_state
- probe_state
- execution_state
- runtime_root
- live_runtime_id
- pending_runtime_id
- torch_import_ok
- torch_cuda_build
- cuda_is_available
- cuda_device_count
- visible_devices
- probe_error_class
- probe_error_message

#### `GPU_QUERY_EXECUTION`
- resolved_device
- embedder_requested_device
- embedder_actual_device
- reranker_requested_device
- reranker_actual_device
- embedder_cuda_peak_mem_before
- embedder_cuda_peak_mem_after
- embedder_cuda_peak_mem_delta
- reranker_cuda_peak_mem_before
- reranker_cuda_peak_mem_after
- reranker_cuda_peak_mem_delta
- fallback_reason

#### `MODEL_BINDING`
- embedder_model_device
- reranker_model_device
- embedder_convert_to_tensor
- reranker_convert_to_tensor
- execution_verified_for_runtime_instance

#### `DEVICE_EXECUTION`
- embedder_stage_planned_device
- embedder_stage_actual_device
- reranker_stage_planned_device
- reranker_stage_actual_device
- embedder_cuda_peak_mem_before
- embedder_cuda_peak_mem_after
- embedder_cuda_peak_mem_delta
- reranker_cuda_peak_mem_before
- reranker_cuda_peak_mem_after
- reranker_cuda_peak_mem_delta
- stage_fallback_reason
- stage_timeout
- stage_elapsed_ms

完成标准：
- 日志足以证明：
  - 为什么 UI 没绿
  - 为什么 query 没上 GPU
  - 是安装、探测还是执行验证先失败

### Phase 2：GPU Runtime 行从“派生灯”收口为“组件 + 能力”双层模型
**状态：已完成（短期收口）**

目标：
- 组件层只描述真实 payload
- 能力层描述用户关心的 GPU 可用能力

建议结构：
- 组件层：
  - `semantic-core`
  - `vector-store`
  - `cuda-runtime`
- 能力层：
  - `markdown-semantic-cpu`
  - `markdown-semantic-cuda`
  - `reranker-cuda`

若短期不做完整拆分，则最少必须：
- 保留当前 `gpu-acceleration` 行
- 但把其内部状态拆成：
  - `install_ok`
  - `probe_ok`
  - `execution_verified`
  - `reason`

短期 UI 必须同时显示：
- 组件是否已落盘到 live runtime
- 当前探测是否通过
- 最近一次执行验证是否通过 / 属于当前 `runtime_instance_id`
- 若为失败，必须直接展示失败原因而不是只显示“需要修复”

完成标准：
- 下载成功与 UI 提示之间关系可解释、可追踪、不再凭感觉

已落地（短期）：
- 保留 `gpu-acceleration` 作为能力行（不强行拆出更多真实组件，避免 UI 复杂度爆炸）
- 能力行内部强制拆分并展示：
  - `install_state`（live runtime 中 payload 是否齐）
  - `probe_state`（基础探测是否通过）
  - `execution_state` + `execution_verified`（主链零下载执行验证是否通过）
- 就绪红线：`gpu-acceleration.ready` 只由 `install_ok AND probe_verified AND execution_verified` 决定
- 诊断字段保持可追踪：`probe_state` 对用户归一化为 `ready`，`execution_state` 保留原始值（例如 `verified`）用于测试/诊断

### Phase 3：Runtime 刷新检测分层与后台化
**状态：已完成**

目标：
- 让 Runtime 刷新从“重量级一把梭”变成：
  - 轻探测
  - 中探测
  - 执行验证

建议模型：
- 轻探测：
  - 页面打开即可跑
  - 只读 manifest / runtime_instance_id / 上次缓存
  - 不 import torch
  - 不触发 CUDA 初始化
- 中探测：
  - 手动点刷新默认执行
  - 后台 worker 执行
  - 包括 torch import、CUDA 小烟雾测试
- 重探测 / 执行验证：
  - 安装后自动触发一次
  - 或用户显式点“执行验证”
  - 必须走同一条 QueryService 主链，而不是另写 helper

实现约束：
- 同一 capability 的探测不能并发；新的刷新请求要么取消旧任务，要么复用已有任务
- 所有中探测 / 重探测必须有超时
- 超时状态记为“未验证/探测超时”，禁止直接硬判为已失败

完成标准：
- 点刷新不会卡 UI
- 用户能区分“未验证 / 正在验证 / 验证成功 / 验证失败”

### Phase 4：零下载 GPU canary 与主链执行验证
**状态：已完成**

目标：
- 不再靠大量真实下载反复点验
- 优先用零下载 canary 证明 GPU 主链可执行

必须具备的 canary：

#### A. `gpu-smoke`
- 同 live runtime context
- import torch
- 分配 CUDA tensor
- 小规模 CUDA 运算
- `torch.cuda.synchronize()`

#### B. `gpu-query-canary`
- 走同一条 QueryService 主链
- 使用极小测试 backend 或极小依赖注入，不依赖真实模型下载
- 证明：
  - QueryService 真把 `cuda` 设备决策贯穿到了执行层

#### C. `gpu-real-model-canary`
- 只在 release gate 做少量真实点验
- 前提：
  - 模型本地已存在
  - `local_files_only=True`
  - `device_policy=require_cuda`

完成标准：
- 先用零下载验证收敛问题
- 真实下载只做少量关键点验

补充要求：
- `gpu-query-canary` 必须与 UI 查询走相同 QueryService / RuntimeContext / workspace/index/filter 解包逻辑
- 禁止另起 helper 进程或独立 Python 脚本做“看起来没问题”的假自检

已落地：
- `gpu-smoke`：`vector_index.probe_runtime_gpu_execution()`（零下载，真实 CUDA tensor/matmul/synchronize）
- `gpu-query-canary`：`runtime_canary.run_gpu_query_canary()`（同 QueryService 主链，注入极小 torch backend，不触发模型下载）
- 查询链证据字段已补齐到 `QUERY_STAGE`（实际设备、fallback reason、stage_ms 等），并有对应单测锁定

### Phase 5：扩展来源目录建库 UX 收尾
**状态：已完成**

目标：
- 每行来源目录都成为一个可理解的独立作业单元

必须补足的行为：
- 已建成来源点击“建库”时，不是直接重跑，而是弹出：
  - 扫描更新
  - 重建
- 进度展示必须补足：
  - 当前阶段（如 scanning / parsing / normalizing / writing_sqlite / writing_vector / finalizing）
  - 当前文件
  - 已处理/跳过/错误数
  - 是否建议关闭软件

建议的阶段状态机：
- `queued`
- `preflight`
- `scanning`
- `parsing`
- `normalizing`
- `chunking`
- `writing_sqlite`
- `writing_vector`
- `finalizing`
- `done`
- `partial_failed`
- `canceled`

完成标准：
- 用户能直观看懂“它在干什么”
- 用户不会因为没有活跃感而误关软件

### Phase 6：稳定性验收与发布门禁
**状态：待开始**

必须通过的验收：
- GPU Runtime 行在真实成功后能稳定转绿
- `device_policy=require_cuda` 的 query 能稳定证明走 GPU
- Runtime 刷新检测不会卡 UI
- 扩展来源目录对已建成来源会先提示“更新还是重建”
- 扩展来源目录建库全过程具备阶段感和活跃感
- 至少要求：
  - 冷启动 3 次
  - 固定 GPU 验证 query 连跑 3 次
  - 安装/修复后重启再验证 1 次
  - CPU-only 路线复验 1 次，确保不被 GPU 改动破坏

## 本计划当前执行记录
### 2026-03-16 当前已完成
- 已冻结范围，不再把外围问题与主问题混修
- 已核实 `详细查询排错日志` 受 `RotatingFileHandler` 轮转限制
- 已核实 `gpu-acceleration` 当前是派生能力行，不是真组件
- 已核实扩展来源目录当前进度提示偏数值化
- 已把高级 AI 的 6 个硬改动正式并入本计划：
  - 漂移指纹
  - 决策级日志
  - 四模式对照
  - `vector-only` 必须走同主链
  - 分层探测
  - 零下载 canary
- 已开始实际执行 Phase 1：
  - `vector_index.py` / `service.py` 已具备 `GPU_CAPABILITY_PROBE`、`GPU_QUERY_EXECUTION` 所需的大部分字段
  - `config_workspace.py` 的 `gpu-acceleration` 行已拆出 `install_state / probe_state / execution_state / execution_verified`
  - 已补 Qt 回归，锁定：
    - “已安装但未做 GPU 执行验证时不能转绿”
    - “只有执行验证通过后，GPU 行才允许转绿”
- 已补查询侧回归，锁定：
  - 当向量召回实际设备为 `cuda:0` 时，`runtime_warnings` 必须包含 `markdown_vector_cuda_ready`
  - 当 reranker 实际设备为 `cuda:0` 时，`runtime_warnings` 必须包含 `markdown_reranker_cuda_ready`
  - `QUERY_STAGE` 必须记录 `vector_actual_device / reranker_actual_device`
- 已完成零下载源码态 Runtime 诊断：仓库根目录 `runtime/` 当前是不完整的，因此源码态探测结果不能再被拿来代表构建版 GPU 可用性；后续 GPU 点验必须坚持“构建版优先”原则
- 已完成当前针对性回归：
  - `tests.test_vector_index`
  - `tests.test_service`
  - `tests.test_reranker`
  - `tests.test_qt_ui`
  - `tests.test_query_runtime`
  - `tests.test_runtime_install_script`
  - `tests.test_launcher`
  - `tests.test_desktop_entry`
  - `tests.test_config`
  - 当前分组回归合计 `148/148 OK`
- 已完成 Phase 3 的第一轮实装收口：
  - `Runtime` 页面不再把“刷新检测”和“GPU 执行验证”混成一个动作
  - `vector_index.runtime_management_snapshot()` 已新增 `verify_gpu=False|True` 双模式：
    - `verify_gpu=False` 只做 live runtime 轻/中探测，并复用缓存的 GPU 执行结果
    - `verify_gpu=True` 才会触发零下载 GPU smoke
  - `ConfigWorkspace` 的 Runtime 刷新现在会复用**同一份** live context snapshot，不再在一次 UI 刷新里重复重探测多次
  - GPU 行新增显式 `执行验证` 按钮；全局 `刷新检测` 继续保留为中探测入口
- 已完成 Phase 5 的核心 UX 收口：
  - 扩展来源目录已支持“已建索引 -> 先提示 `扫描更新 / 重建`”
  - PDF / Tika 行级进度现在按阶段展示，而不再只是百分比：
    - `scan_sources`
    - `parse_pdf / parse_tika`
    - `write_vector`
    - `finalizing`
  - 行级进度会同步展示当前文件、处理数、跳过数、错误数、删除数，以及“当前是否建议关闭软件”
  - 行空闲时会显示已索引摘要（文件数 / chunk 数 / 向量文档数），避免用户误以为系统没在记住历史结果
- 已补本轮关键防回归：
  - `tests.test_vector_index::test_runtime_management_snapshot_reuses_cached_gpu_probe_until_verification_is_requested`
  - `tests.test_qt_ui::test_config_workspace_runtime_refresh_reuses_one_context_snapshot`
  - `tests.test_qt_ui::test_config_workspace_gpu_runtime_actions_show_verify_button`
  - 扩展来源目录阶段化进度与“更新/重建”相关用例继续保持通过
- 本轮分组回归结果：
  - `tests.test_vector_index`
  - `tests.test_qt_ui`
  - `tests.test_service`
  - `tests.test_extensions`
  - `tests.test_runtime_install_script`
  - `tests.test_launcher`
  - `tests.test_desktop_entry`
  - `tests.test_config`
  - 当前分组回归合计 `158/158 OK`

- 已修复“只在整套跑时失败”的跨测试污染：
  - 根因：`tests/test_service_import.py` 会清理 `sys.modules` 中的 `omniclip_rag.extensions*` 但不恢复，导致后续 `patch('omniclip_rag....')` 作用在新模块对象，实际代码仍引用旧对象，最终 patch 失效并触发真实 HTTP / 健康检查
  - 修复：该测试改为 `try/finally` 恢复原模块对象，保证后续测试看到一致的 import graph
- 已新增 `pytest.ini` 约束 `testpaths=tests`，避免仓库内 `local_appdata/`、`dist/` 等运行时目录被 pytest 递归扫描触发权限错误
- 全量回归：`224/224 OK`

### 2026-03-16 下一步只做什么
1. 进入 Phase 6（构建版验收门禁）：
   - 用构建版 onedir 做 GPU 真实验收（禁止用源码态 runtime 结果代替）
   - 依次跑：
     - `--selfcheck-acceleration gpu-smoke`
     - `--selfcheck-acceleration gpu-query-canary`
     - `--selfcheck-acceleration suite`
   - 在 Runtime 页确认：GPU 行在执行验证通过后稳定转绿；刷新检测不再卡 UI
2. 若 Phase 6 在实机仍出现“下载成功但仍红 / 查询仍未上 GPU”：
   - 只沿本计划 Phase 6 的指纹与证据字段排查（禁止回到散修）
3. 最后再考虑 Phase 2 的“完整组件层 + 能力层”拆分（属于锦上添花，不再阻塞当前发布）

## 新窗口 / 新 AI 接手规则
接手时必须按这个顺序做：

1. 先读本计划
2. 再读 [Markdown主查询与Runtime稳定性RCA计划.md](/D:/软件编写/OmniClip%20RAG/plans/Markdown%E4%B8%BB%E6%9F%A5%E8%AF%A2%E4%B8%8ERuntime%E7%A8%B3%E5%AE%9A%E6%80%A7RCA%E8%AE%A1%E5%88%92.md)
3. 再读 [ARCHITECTURE.md](/D:/软件编写/OmniClip%20RAG/ARCHITECTURE.md)
4. 然后只沿当前 Phase 往下推进，不得跳回散修
