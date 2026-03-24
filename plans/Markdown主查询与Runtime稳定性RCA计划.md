# Markdown 主查询与 Runtime 稳定性 RCA 计划

## 目标
把当前最核心、最反复、最耗时的故障收束为一条唯一战线：

- 让 **构建版 EXE** 的 **Markdown 主查询** 恢复到可验证的高级搜索状态
- 不再用“runtime 看起来完整 / 能 import”替代“这次查询真的执行了语义召回”
- 不再四处散乱修外围问题，而是按固定阶段做 RCA、证明、修复、回归
- 让新窗口或新 AI 接手时，能直接沿本计划继续，不再重复走旧弯路

## 适用范围
本计划只处理以下主问题：

1. 构建版 EXE 中，Markdown 查询看起来没有真正走高级语义搜索
2. Runtime 多次下载/修复后，查询行为仍像 lexical-only 或候选为 0
3. 查询日志里的“CPU 语义检索正常”与真实查询结果不一致

本计划 **暂时不处理**：

- Tika 扩展格式质量优化
- PDF 召回质量优化
- UI 体验类细节（除非影响主查询 RCA）
- 非 Markdown 主链的次级问题

## 当前唯一验收对象
- 只验收 **构建版 EXE**，不再以源码态窗口作为最终依据
- 当前主点验路径：
  - `D:\Apps\OmniClip RAG\dist\OmniClipRAG-v0.2.4\OmniClipRAG.exe`
- 当前固定复现场景：
  - 笔记库：`<user-logseq-vault>`
  - 查询词：`我的思维`
  - 阈值：`0`
  - 条数：`30`

## 背景信息
### 项目结构
- Python + PySide6 桌面应用
- PyInstaller onedir 打包
- 主程序：Markdown 本地索引 + 查询
- 扩展：PDF 独立链、Tika sidecar 独立链
- 数据目录默认在 `%APPDATA%\OmniClip RAG`
- Runtime 为外置，不打进主 EXE

### 当前和本问题直接相关的关键文件
- 主查询：`omniclip_rag/service.py`
- Runtime 探测与导入环境：`omniclip_rag/vector_index.py`
- Runtime 布局与 pending/live 切换：`omniclip_rag/runtime_layout.py`
- 启动器：`launcher.py`
- 查询台 UI：`omniclip_rag/ui_next_qt/query_workspace.py`
- Runtime 管理页：`omniclip_rag/ui_next_qt/config_workspace.py`
- Runtime 安装脚本：`scripts/install_runtime.ps1`

### 当前已知的重要事实
- `Tika` 与 Markdown 主查询是两条线，不应互相影响
- `BAAI/bge-m3` 模型下载链与 Runtime 修复链是两条线，不应互相影响
- `无 N 卡` 不等于不能做 CPU 语义检索
- 当前用户最痛的点，不是“不能下载”，而是“下载后查询行为仍像没用上高级搜索”

## 反复出现的核心问题
### 问题 A：能力健康不等于本次查询真的执行了语义召回
现象：
- 日志会出现“当前 Markdown 语义检索正在使用 CPU 正常运行，暂未启用 N卡 / CUDA 加速”
- 但查询结果却是 0 条，或看起来像完全没走高级语义链

当前判断：
- 这更像 `capability healthy != actual query executed vector retrieval`
- 需要证明的是“这次查询到底有没有真的跑 vector”

### 问题 B：Runtime 修复成功后，查询行为仍可能异常
现象：
- 下载 / 安装终端显示成功
- Runtime 页过去长期仍显示“需要修复”
- 或者即使显示就绪，查询结果仍不像语义检索

当前判断：
- 不能再以“导入成功”或“目录存在”作为完成标准
- 必须改成查询端到端可证明

### 问题 C：之前修复一直在绕圈
循环模式大致是：
- 补运行时探测
- 修下载脚本
- 修 pending/live
- UI 显示稍好
- 真实查询仍不像高级搜索
- 再回头修 runtime

当前判断：
- 之前同时在修三条线：
  - Runtime 安装/应用
  - Runtime 健康探测
  - 真实查询执行
- 但第三条线没有被钉死证明，所以会反复绕回去

## 之前已经做过什么
以下内容已经做过，不应再从零开始重复：

1. 扩展格式子系统已完成，且与 Markdown 主链隔离
2. Tk 老界面已移除，Qt 是唯一桌面 UI
3. Runtime 已改成 componentized / pending-aware 的方向
4. 手动/自动 Runtime 修复命令已统一为同一个脚本
5. 查询台已经支持记录 `trace_lines`
6. `配置 -> 数据` 已新增“把详细查询排错日志写入活动日志”的持久化开关
7. 查询 trace 目前可写入普通活动日志，并继续受日志单文件上限控制
8. 构建版与源码态曾发生混用；源码态窗口已明确标识 `[开发态]`

## 当前最可能的根因排序
1. QueryWorker / QueryService 实际读取的 `workspace / index / profile / scope` 不正确
2. 语义召回在 query-time 根本没有执行，或执行后异常被静默 fallback 吞掉
3. 查询链仍被 lexical seed 卡死，尤其是中文查询词
4. Runtime 健康探测和真实查询执行链看到的环境仍有偏差
5. UI 只是放大混乱，不是主根因

## 当前禁止事项
为了防止继续散乱试错，从本计划开始，以下行为视为禁止：

- 不允许再优先修 Tika / PDF / 扩展格式问题
- 不允许再优先修启动动画、表格样式等体验问题
- 不允许再仅靠“runtime import 成功”宣布问题已修复
- 不允许在没有固定复现场景的情况下切换查询词和库
- 不允许再次把源码态窗口结果当成构建版最终结论
- 不允许继续同时推进多条不相干修复线
- 不允许在 QueryWorker 之外另写一套 `vector-only` / `selftest helper` 作为主结论依据
- 不允许以“一次成功”作为收官，后续验收至少要求冷启动与固定 query 重复验证

## 唯一推进路径
### Phase 0：冻结范围与诊断基线
**状态：已完成**

目标：
- 把问题收束到 Markdown 主查询
- 只点验构建版 EXE
- 固定唯一复现用例

完成标准：
- 任何后续修复都必须围绕“为什么这次 query 没真正执行高级搜索”来展开
- 新窗口/新 AI 先读本计划，再继续做事

当前结果：
- 已固定主点验对象、笔记库、查询词、阈值和条数
- 已明确暂停外围问题

### Phase 1：结构化日志对照落地
**状态：已完成**

目标：
- 让每次查询都能对照“应该做什么 / 实际做了什么”
- 不是泛泛日志，而是结构化字段

必须记录的三组日志：

1. `QUERY_PLAN`
- query_id
- query_text
- query_mode（`lexical-only | vector-only | hybrid_no_rerank | hybrid`）
- allowed_families
- lexical_enabled
- vector_enabled
- vector_mode
- seed_strategy（至少区分 `pure_vector` 与非 pure vector）
- reranker_enabled
- threshold
- topk
- profile_hash

2. `QUERY_FINGERPRINT`
- pid
- thread_name
- sys.executable
- sys._MEIPASS
- cwd
- app_root
- runtime_root
- runtime_manifest_version
- live_runtime_id
- pending_runtime_id
- runtime_instance_id
- appdata_root
- workspace_id
- workspace_realpath
- index_generation_id
- index_built_for_workspace
- index_built_at
- sqlite_realpath
- vector_db_realpath
- fts_rows_in_scope
- vector_rows_in_scope
- embedding_model_id
- reranker_model_id
- build_id
- exe_version

3. `QUERY_STAGE`
- query_text_normalized
- fts_query_normalized
- lexical_candidates_raw
- vector_query_planned
- vector_query_executed
- vector_table_ready
- vector_backend
- vector_candidates_raw
- vector_exception_class
- vector_exception_message
- fusion_candidates_raw
- reranker_applied
- reranker_skip_reason
- final_candidates_raw
- final_after_filters
- postfilter_drop_count
- fallback_reason（如果发生）
- stage_ms.lexical / vector / fusion / rerank / finalize

说明：
- 日志写入受 `配置 -> 数据 -> 把详细查询排错日志写入活动日志` 开关控制
- 查询台仍可显示 trace，不受该开关影响

完成标准：
- 对同一个 query，日志足以回答“哪一步先归零”

### Phase 2：四模式强制对照验证
**状态：已完成**

目标：
- 不能只看 hybrid
- 必须把 query 切成四种模式验证：
  - lexical-only
  - vector-only
  - hybrid_no_rerank
  - hybrid

判定规则：
- lexical-only = 0 且 vector-only > 0：lexical / 中文 tokenizer / FTS seed 问题
- lexical-only > 0 且 vector-only = 0：vector 表 / scope / query-time vector 执行问题
- 两边都 0：workspace / index / config / scope 错
- lexical-only > 0 且 vector-only > 0，但 hybrid_no_rerank = 0：fusion / post-filter 问题
- hybrid_no_rerank > 0 但 hybrid = 0：reranker 或 rerank 后过滤问题

说明：
- `vector-only` 必须走同一个 `QueryService`、同一个 `RuntimeContext`、同一个 workspace/index/filter，只把候选策略切到 pure vector；禁止另写 helper
- 这一步比继续修 Runtime UI 更优先

完成标准：
- 对固定 query，能明确回答到底是哪一层先归零

### Phase 3：环境对齐证明
**状态：已完成**

目标：
- 证明健康探测与真实查询使用的是同一个运行时根、同一个索引根、同一个 workspace

必须新增或核对的硬约束：
- 启动器只生成一次 canonical `RuntimeContext`
- `runtime_instance_id` 必须在：
  - 启动器
  - 健康探测
  - QueryWorker 查询开始
  三处一致
- EXE 同级 runtime 根必须锚定 `Path(sys.executable).parent / "runtime"`
- `sys._MEIPASS` 只允许作为 bundle 资源根被记录，不能替代 sidecar runtime 根
- 不允许健康探测和真实查询各自重新发现 runtime/index/workspace
- 如果任何链路会起子进程，必须额外记录 DLL 搜索环境、child-process 策略和是否做过环境清洗

完成标准：
- failing query 上的 `RuntimeContext` 指纹完全可追踪

### Phase 4：根因修复
**状态：已完成**

目标：
- 只修已被 Phase 1-3 证明的真正根因
- 不再凭感觉大范围改

可能的修复方向，只能在证据支持下二选一或三选一：
- workspace/index/scope 读取错误
- query-time vector 执行被 fallback 吞掉
- lexical seed / 中文 FTS 链路卡死
- fusion / post-filter 逻辑错误

完成标准：
- 固定 query 在构建版上恢复可解释的高级搜索结果

### Phase 5：端到端自检收尾
**状态：进行中（构建版 suite 已绿，GUI 查询链旧配置偏差已修，canary 与最终点验待补）**

目标：
- 以后不再让用户靠真实下载和人工反复试错来验证主链

最少要有 5 个 canary：
1. lexical canary
2. 中文 lexical/tokenizer 对照 canary
3. pure vector canary
4. reranker canary
5. hybrid canary

要求：
- 自检必须走和 UI 查询相同的主入口
- 不允许另写一套“看起来健康”的 helper 替代主链

完成标准：
- 未来只要主查询链退化，canary 会先报出来

## 当前阶段更新记录
### 2026-03-16（构建版 suite 四模式最终钉死）
- 已直接在当前验收构建物 `dist/OmniClipRAG-v0.2.4/OmniClipRAG.exe` 上，对真实工作区 `<user-logseq-vault>` 执行 `--selfcheck-query --query-mode suite`，不再依赖源码态入口或开发态运行环境。
- 本次固定 query `我的思维` 的构建版四模式结果已经全部落盘到 `.tmp_dist_runtime_diag_packaged_suite.json`，关键事实如下：
  - `lexical-only`：`result_count=0`，`lexical_candidates_raw=0`
  - `vector-only`：`result_count=30`，`vector_query_executed=true`，`vector_candidates_raw=300`
  - `hybrid_no_rerank`：`result_count=30`
  - `hybrid`：`result_count=30`，`reranker_applied=true`，`candidate_count=120`
- 这说明当前构建版 Markdown 主查询已经**真实执行了 CPU 语义召回与 reranker**；此前“看起来像完全没走高级搜索”的主病灶已被切开，不再是 Runtime 安装链或 capability probe 本身的问题。
- 进一步确认的结构根因链如下：
  1. 进程内 semantic probe 之前会清空已加载模块并重新拉起 C 扩展，触发 `cannot load module more than once per process` 一类假故障；现在 probe 已改为纯观察式，不再破坏解释器状态。
  2. frozen EXE 对外置 runtime wheels 的 stdlib 支撑面不足，真实 query-time import 会在 `timeit / pdb / http.cookies / asyncio.base_events` 等模块上跌倒；现已通过打包白名单与运行时预加载补齐。
  3. `LanceDbVectorIndex` 之前过早拉起 vector-store 栈、`SentenceTransformer(...)` 实例化又发生在 runtime context 外，导致“能力探测健康、真实 query 失败”的错位；现在两处都已收口。
- 当前剩余问题已经收缩为两个明确方向，而不是 Runtime 继续“莫名其妙坏掉”：
  - 中文 lexical/FTS 对照为什么对 `我的思维` 返回 0
  - GUI 点击链是否与 `--selfcheck-query` 主链完全一致（若不一致，只允许继续查 QueryWorker / UI 参数传递，不允许再回头修下载器）
- 从本记录开始，后续任何推进都必须以 **构建版 suite 结果为第一证据**；禁止再把“用户肉眼感觉不像高级搜索”当成唯一诊断来源。

### 2026-03-16（GUI 查询链旧配置偏差修复）
- 已确认 GUI 查询链里存在一个结构性偏差：
  - `QueryWorkspace._validate_query_request()` 默认只读取自己缓存的 `self._config / self._paths`
  - 当用户在 `配置 -> 开始` 页切换笔记库但尚未经过完整保存/回灌时，查询页可能继续拿旧 workspace 去查
  - 这能直接解释“构建版 `--selfcheck-query` 绿、GUI 点同一 query 却像没走正确索引”的分裂现象
- 已落地修复：
  1. `QueryWorkspace` 新增 `runtime_snapshot_provider`
  2. 每次点击查询前，查询页都会实时向配置页拉取当前 live snapshot
  3. `ConfigWorkspace` 暴露 `current_runtime_snapshot()` 作为查询用唯一入口
  4. `MainWindow` 显式把 Query/Config 两页接通，不再只依赖 `runtimeConfigChanged` 的被动同步
- 针对性回归：
  - 新增 Qt 用例，证明当 QueryWorkspace 缓存旧 vault、provider 返回新 vault 时，查询准备阶段会优先采用新 vault
  - `tests.test_qt_ui` 相关 3 条 + 主查询链相关 46 条针对性回归已通过
- 当前剩余：
  - 只差构建版 GUI 最终点验，确认实际点击查询时结果与构建版 suite 一致

### 2026-03-16（构建版主链 RCA 收口）
- 已在 **零下载、真实工作区、构建版 RuntimeContext** 下最终证明：`我的思维` 这条 failing query 之前不是“高级搜索没装好”，而是 **`LanceDbVectorIndex` 的初始化顺序错误**。
- 精确根因已经钉死为两层：
  1. `LanceDbVectorIndex.__init__()` 与状态探测过早导入 `lancedb / pyarrow / pandas / numpy`，让向量库栈先污染进程；
  2. `_default_embedder_factory()` 只把 `from sentence_transformers import SentenceTransformer` 放在 runtime context 里，但真正的 `SentenceTransformer(...)` 模型实例化发生在 context 外，导致本地模型权重加载阶段看不到完整的 runtime 路径和 DLL 环境。
- 这就是为什么此前会长期出现：Runtime/UI/加速探测都显示“CPU 语义检索正常”，但实际 query 仍然 `vector_query_failed`。
- 结构修复已落地：
  1. `LanceDbVectorIndex` 改成 **真正的 lazy vector-store bootstrap**：初始化和状态读取不再提前连接 LanceDB；只有在真正打开表或写表时才导入 `lancedb`。
  2. `_table_exists()` 现在优先走已连接 DB，否则回退到磁盘 `.lance` 目录存在性判断；这样 query 前状态探测不会把 vector-store 栈提前拉进进程。
  3. `_default_embedder_factory()` 现在把 **`SentenceTransformer(...)` 的完整实例化** 也包进 `_runtime_import_environment(component_id='semantic-core')`，不再只包 import 语句。
- 修复后的零下载构建版 suite 自检结果：
  - `lexical-only`：`result_count=0`，说明这个中文 query 的词法链本身没有候选；
  - `vector-only`：`result_count=30`，`vector_candidates_raw=300`；
  - `hybrid_no_rerank`：`result_count=30`；
  - `hybrid`：`result_count=30`，`reranker_applied=true`。
- 这证明 Markdown 主查询的 **CPU 高级语义搜索 + reranker 主链已恢复**；当前剩余问题不再是 runtime 安装或 query-time vector 执行，而是后续是否需要单独优化中文 lexical/FTS 表现和 UI 呈现。
- 从这个节点开始，Phase 4 已完成；Phase 5 进入“端到端 canary / 构建版点验 / 交付稳定化”阶段。

### 2026-03-16
- 已对固定 failing query 在真实工作区上完成四模式主链对照，且明确使用构建版 RuntimeContext：
  - `lexical-only`：`lexical_candidates_raw=0`
  - `vector-only`：最初表现为 `vector_query_executed=true` 但 `TypeError: Unsupported query type: <class 'numpy.ndarray'>`
  - `hybrid_no_rerank / hybrid`：同样命中 `vector_query_failed`，说明问题不在 reranker，而在 query-time vector adapter
- 已进一步证明环境对齐问题确实存在：`semantic-core` 组件导入时原本被旧 flat `runtime/` 根目录里的残留坏 `transformers` 抢占，导致真实查询链和健康探测链看到的模块来源不一致。
- 根因修复已落地两刀：
  1. `vector_index._runtime_import_environment()` 与 `_probe_runtime_semantic_core()` 现在会让 `semantic-core` 自动带上 `vector-store` 依赖，并保证 `semantic-core` 的 `sys.path` 优先级高于 legacy flat runtime。
  2. `LanceDbVectorIndex.search()` 现在会把 query-time embedding 向量统一强制转换成 `list[float]`，不再把 `numpy.ndarray` 直接交给 LanceDB。
- 修复后再次对真实工作区跑四模式，结果已经收口为：
  - `lexical-only`：仍为 0，这证明当前 failing query 的词法链本身对该中文词组没有候选
  - `vector-only`：`count=30`，`vector_candidates_raw=300`，`vector_query_executed=true`
  - `hybrid_no_rerank`：`count=30`，语义召回正常
  - `hybrid`：`count=30`，`reranker_applied=true`
- 这说明当前主问题已经从“高级搜索完全失效”推进到“高级搜索主链恢复，但中文 lexical/FTS 对照仍值得单独分析”，后续不该再回头围着 Runtime UI 猜。
- 已决定停止围绕 Runtime UI / 扩展格式 / 启动体验做散乱修复
- 已把排错日志开关接入 `配置 -> 数据`
- 已吸收高级 AI 审核意见：补 build/runtime/index 漂移指纹、把三模式升级为四模式、明确 `vector-only` 禁止走 helper，并补充稳定性验收禁止事项
- 已明确下一步只允许推进结构化查询日志、四模式对照和环境对齐证明
- Phase 1 已在 `service.py` 落地：`QUERY_PLAN / QUERY_FINGERPRINT / QUERY_STAGE` 结构化 payload 已生成并复用现有 `trace_lines` 输出；最小临时工作区 probe 已验证真实 query 会输出这三组日志。
- `app_entry/desktop.py --selfcheck-query --query-mode <mode|suite>` 已接上同一条 QueryService 主链，可用于后续四模式对照，不再需要额外 helper。
- `--query-mode suite` 的四模式零下载自检已经在最小临时工作区上跑通；下一步只需要对固定 failing query 跑同一条构建版主链，就能直接看出哪一步先归零。
- 已用真实工作区对固定 query 跑过一次四模式源码入口自检：`lexical-only / vector-only / hybrid_no_rerank / hybrid` 全部为 0，其中 `lexical-only` 的 `lexical_candidates_raw=0`，而其余三种模式都表现为 `vector_query_planned=true` 但 `vector_query_executed=false`、`fallback_reason=vector_runtime_unavailable`。这证明了“能力健康提示”和“这次查询实际执行了语义召回”必须拆开看。注意：这条证据仍来自源码入口，不是构建版最终验收；构建版自检产物写出链还需要单独钉死。

## 当前最想问接手 AI 的问题
1. 当前构建版里，`CPU 语义检索正常` 为什么不能推出 `这次查询真的执行了 vector retrieval`？最可能断在什么位置？
2. 在 PyInstaller onedir + EXE 同级 runtime 下，怎样保证健康探测和真实查询执行使用完全一致的 `RuntimeContext`？
3. 对中文查询词 `我的思维`，现在更值得优先怀疑 lexical seed 问题，还是 vector 执行 / scope 问题？为什么？
4. 一个真正可信的端到端语义检索自检，应如何设计成能证明“高级搜索真的参与了这次查询”？

## 交接要求
任何新窗口 / 新 AI 接手时，必须先做这几件事：

1. 阅读本文件
2. 阅读 `ARCHITECTURE.md` 最后关于 Runtime / Markdown 主查询 / 构建版验收的记录
3. 只使用构建版 EXE 作为最终点验对象
4. 先跑固定 query：
   - 库：`<user-logseq-vault>`
   - 词：`我的思维`
   - 阈值：`0`
   - 条数：`30`
5. 先看 `QUERY_PLAN / QUERY_FINGERPRINT / QUERY_STAGE`
6. 在没有证据前，禁止继续改 Tika / PDF / UI 体验

## 最终验收标准
只有同时满足以下条件，才能宣布这条问题收官：

- 构建版 EXE 中，固定 query 不再表现为假高级搜索
- 能清楚证明 query-time vector 实际有执行，而不是只有 capability healthy
- CPU-only 环境下也能稳定使用高级语义搜索
- reranker 在候选足够时实际应用
- 后续回归能由 canary / 结构化日志先发现，而不是用户人工反复点验后才发现
