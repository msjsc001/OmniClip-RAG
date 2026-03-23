# Tika 建库稳定性与安装进度闭环计划

## 文档目的

这份文档用于固化 `0.3.2` 之后 Tika 路线的一次关键修复，避免后续窗口、后续 AI 或后续版本再次回到 `XHTML-only` 的错误实现。

它记录三类信息：

1. 我们当时遇到了什么真实问题
2. 根因最终是什么
3. 代码已经如何落地、如何验收、后续还应遵守什么边界

---

## 背景

`OmniClip RAG` 当前的扩展格式路线分为两条：

- `PDF`：独立解析链，不走 Tika
- `Tika`：用于除 PDF 外的大量扩展格式，含推荐、未知、未测试与兼容性差分组

在 `0.3.2` 版本中，Tika 格式选择窗已经支持大规模格式暴露，但实际建库时用户反馈：

1. 选择了 `EPUB` 格式，并在来源目录中放入有效文件，点击“建库”后所有文件都被跳过
2. `Tika` 下的“自动安装”没有页内进度提示，用户无法判断是否已经开始、进行到哪一步、还要多久

用户实际测试目录可抽象为：

- `%USERPROFILE%\Downloads\sample-tika-corpus`

其中包含：

- `sample.epub`：有效 EPUB 文件
- `新建 Microsoft Word 文档.docx`：0 字节空文件
- `油画材料学-ocr.pdf`：PDF，按架构不应走 Tika

---

## 已确认的根因

### 根因一：不是“没扫到文件”，而是“命中的文件都解析失败”

活动日志与实机对照后确认：

- Tika sidecar 已正常启动并健康检查通过
- 预检已正确识别到命中的格式与文件
- 真正失败发生在解析阶段

当时代码强依赖：

- `PUT /tika`
- `Accept: application/xhtml+xml`

在用户当前的 `Tika 3.2.3` 运行时下，这套请求对 `EPUB/DOCX` 返回 `HTTP 406`。

这意味着：

- 不是文件坏了
- 不是 Tika 不支持该格式
- 而是我们的对接方式错误

后续实机验证已证明：

- `PUT /tika + Accept: text/plain` 可以成功解析 `抗炎食物.epub`
- `PUT /rmeta + Accept: application/json` 也可作为后备策略

### 根因二：自动安装进度后端已具备，前端没有接入

后端 `install_tika_runtime()` 早已支持：

- 阶段回调
- 下载字节数
- 当前下载项

但前端配置页仍使用 `FunctionWorker`，没有把进度事件接到 Runtime 卡片，所以用户只能看到“开始”和“结束”，中间全黑盒。

---

## 已落地的修复方案

### 阶段 1：Tika 解析策略改为兼容优先

状态：已完成

落地内容：

- `parse_file_with_tika()` 不再返回单一 XHTML 字符串
- 新增结构化结果 `TikaParsedContent`
- 固定解析顺序为：
  1. `PUT /tika + Accept: text/plain`
  2. 若失败、为空或不兼容，则回退到 `PUT /rmeta + Accept: application/json`
  3. 若两者都失败，则抛出结构化 `TikaParseError`

边界约束：

- `XHTML` 不再是 Tika 路线的默认成功条件
- Tika 路线的成功条件改为：**能稳定拿到可规整为正文的内容**
- 未来新增、未测试、兼容性差的 Tika 格式，默认都走这一条统一兼容链

### 阶段 2：统一正文归一化，不再绑死 XHTML

状态：已完成

落地内容：

- 新增 `normalize_tika_content(...)`
- 支持：
  - `text/plain` -> 空行切段并规整正文
  - `application/json` -> 从 `X-TIKA:content` / `content` 等字段提取正文再规整
  - `xhtml/xml` -> 继续兼容旧的 XHTML 归一化器

边界约束：

- `normalize_tika_xhtml()` 保留，但不再是唯一入口
- 后续如果 Tika 某些格式只愿意稳定给出纯文本或 rmeta JSON，不需要再做专门特判

### 阶段 3：构建链区分“正常跳过”和“真实失败”

状态：已完成

落地内容：

- Tika 构建结果拆分为三类：
  - `matched_and_indexed`
  - `matched_but_skipped_expected`
  - `matched_but_failed`
- `expected skip` 仅用于：
  - 空文件
  - 不可读取文件
  - 提取后无正文
- `failed` 用于：
  - Tika 请求失败
  - 返回格式异常
  - 所有兼容策略都失败

同时新增：

- `TikaBuildReport.expected_skips`
- `TikaBuildReport.failed_files`
- `TikaBuildReport.recent_issues`
- `TikaPreflightReport.recent_issues`

UI 与日志效果：

- 不再只显示“跳过 N”
- 若全部命中文件都失败，会明确提示“已命中但全部解析失败”
- 会附带最近失败原因摘要，例如：
  - `空文件 · 新建 Microsoft Word 文档.docx`
  - `HTTP 406 · 某某.epub`

### 阶段 4：Tika 自动安装加入页内进度

状态：已完成

落地内容：

- `_install_tika_runtime_requested()` 改用 `ProgressFunctionWorker`
- Tika Runtime 卡片新增页内进度区：
  - 状态文字
  - 进度条
  - 当前阶段/当前文件/字节数/安装目录

固定展示阶段：

- 准备目录
- 下载 Tika Server
- 下载 Java
- 解压 Java
- 完成校验

交互约束：

- 安装中自动安装按钮禁用
- 安装中重新检测按钮禁用
- 安装完成后状态区切回完成态摘要
- 安装失败后保留失败原因

---

## 关键设计结论

### 1. Tika 路线必须“正文优先”，不是“结构优先”

这次问题已经证明，若把 Tika 扩展链建立在“必须得到 XHTML”上，会造成：

- 有效文档被整批误判失败
- 用户误以为 Tika 不支持该格式
- 新增格式暴露再多也没有实际意义

因此长期约束已经固定：

- Tika 路线优先保证“可稳定提取正文并建库”
- 而不是追求所有格式都保留复杂结构

### 2. “跳过”必须分语义

以后只要是扩展链，`skip` 不允许继续混成一个桶。

必须明确区分：

- 正常可解释跳过
- 真失败

否则用户无法判断是：

- 文件本身问题
- 解析器兼容问题
- 还是程序根本没工作

### 3. 后端已有进度事件，不接到 UI 等于没做

安装类任务的最小可用标准已经固定：

- 用户点击后必须立即出现可见反馈
- 页面内必须能看到“当前阶段”
- 对可测量的下载过程必须给出百分比与字节数

---

## 实际验收结果

### 自动化测试

本轮已补充并通过的验证包括：

- `parse_file_with_tika()` 纯文本优先成功
- `parse_file_with_tika()` `rmeta/json` 回退成功
- `normalize_tika_content()` 对纯文本与 rmeta JSON 的归一化
- Tika 构建链遇到空文件时记为 expected skip
- Tika 行级进度能带出 recent issue
- Qt 配置页中 Tika 安装已切换到进度 worker，并能显示页内进度

总自动化结果：

- `240` 项测试通过

### 实机验证

已用真实 sidecar 对以下文件验证：

- `%USERPROFILE%\Downloads\sample-tika-corpus\sample.epub`

结果：

- `text/plain` 策略成功
- 能返回有效中文正文内容
- 证明当前问题确实已从“全部解析失败”切换为“兼容优先可用”

同时确认：

- `新建 Microsoft Word 文档.docx` 为 `0` 字节空文件
- 该文件应继续被解释为“正常跳过”，而不是 Tika 整体失败

---

## 当前已完成状态

本计划所有既定代码目标均已完成：

- [x] 兼容优先多策略解析
- [x] 统一正文归一化入口
- [x] expected skip / failed 语义拆分
- [x] recent issue 回写 UI 与日志
- [x] Tika 自动安装页内进度
- [x] 自动化测试补齐
- [x] 实机 EPUB 验证

---

## 后续硬约束

后续如果继续扩展 Tika 路线，必须遵守：

1. 不能再把 Tika 主链实现回 `XHTML-only`
2. 新格式默认走统一兼容优先链
3. 构建 UI 不能只报“跳过 N”，必须带出原因摘要
4. 安装/下载类动作必须有页内可见进度
5. PDF 继续保持独立路线，不与 Tika 混线

---

## 关联文件

- `omniclip_rag/extensions/runtimes/tika_runtime.py`
- `omniclip_rag/extensions/normalizers/tika_output.py`
- `omniclip_rag/extensions/parsers/tika.py`
- `omniclip_rag/extensions/service.py`
- `omniclip_rag/ui_next_qt/config_workspace.py`
- `omniclip_rag/ui_i18n.py`
- `tests/test_tika_extension.py`
- `tests/test_extensions.py`
- `tests/test_qt_ui.py`
