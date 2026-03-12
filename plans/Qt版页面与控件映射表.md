# Qt 版页面与控件映射表

## 目标

本映射表用于把旧版 Tk UI 的每一块真实界面与交互，明确映射到新版 `PySide6 + Qt Widgets` 的目标实现。目标不是“找一个差不多的控件替代”，而是保证：

- 布局与交互语义完整继承；
- 性能与稳定性明显优于 Tk；
- UI 与后端继续保持高解耦；
- 新旧 UI 并存期间互不污染状态。

---

## 一、全局壳层与导航映射

| 旧 UI 位置 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 根窗口 | `tk.Tk()` | `QMainWindow` | 统一承载菜单、中心区、状态栏、几何恢复。 |
| 根窗口标题 | `root.title(...)` | `QMainWindow.setWindowTitle(...)` | 保持标题文案一致。 |
| 默认尺寸 / 最小尺寸 | `root.geometry` + `root.minsize` | `resize()` + `setMinimumSize()` | 保持首次启动与最小尺寸一致。 |
| 程序图标 | `iconphoto` / `iconbitmap` | `QApplication.setWindowIcon()` | 从 `resources` 统一加载。 |
| 顶栏容器 | `tk.Frame + grid` | `QWidget + QGridLayout` | 更稳定地控制标题区与右侧控件。 |
| 顶栏图标 | `tk.Label(image=...)` | `QLabel` | 使用 `QPixmap` 缩放显示。 |
| 顶栏标题 / 副标题 / 引导语 | `tk.Label` | `QLabel` | Qt 版使用 `wordWrap=True`，不再手工算 wraplength。 |
| 语言切换 | `ttk.Combobox(state='readonly')` | `QComboBox` | 改为信号驱动刷新翻译层。 |
| 版本徽标 | `tk.Label` | `QLabel` | 可用自定义 `QFrame` 样式或 `QLabel` + QSS。 |
| 顶层主页签 | `ttk.Notebook` | `QTabWidget` | 负责 `查询 / 配置` 顶层切换。 |
| 页脚状态区 | `tk.Frame + 2 Label` | `QStatusBar + QLabel` | 左侧普通状态，右侧 permanent widget 显示结果摘要。 |

---

## 二、查询页映射

### 1. 查询页骨架

| 旧 UI 位置 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 查询页整体 | 主页签内 `Frame` | `QWidget` | 作为 `QTabWidget` 第一个页面。 |
| 搜索区 / 结果区分隔 | `ttk.Panedwindow(orient='vertical')` | `QSplitter(Qt.Vertical)` | 必须使用 `saveState()/restoreState()` 记忆布局。 |
| 搜索卡 / 结果卡 | `tk.Frame + card 样式` | `QFrame` | 统一卡片基类，轻量 QSS。 |

### 2. 搜索卡

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 查询状态提示条容器 | `tk.Frame` | `QFrame` | 顶部右对齐，使用状态色切换。 |
| 查询状态标题 | `tk.Label(textvariable=...)` | `QLabel` | 使用 ViewModel 驱动文本。 |
| 查询状态详情 | `tk.Label(textvariable=...)` | `QLabel` | 保留多行详情显示。 |
| 查询提示文案 | `tk.Label` | `QLabel` | 允许自动换行。 |
| 查询输入框 | `ttk.Entry` | `QLineEdit` | `returnPressed` 触发查询。 |
| 查询按钮 | `ttk.Button` | `QPushButton` | 普通查询动作。 |
| 查询并复制按钮 | `ttk.Button` | `QPushButton` | 触发查询并复制。 |
| 复制上下文按钮 | `ttk.Button` | `QPushButton` | 复制当前重组上下文。 |
| 分数阈值输入 | `ttk.Entry` | `QLineEdit` 或 `QDoubleSpinBox` | 若强调兼容旧输入习惯，优先 `QLineEdit + 校验器`。 |
| 查询条数输入 | `ttk.Entry` | `QLineEdit` 或 `QSpinBox` | 建议 `QLineEdit + 校验器`，避免与旧配置格式冲突。 |
| 查询条数提示 | `tk.Label` | `QLabel` | 与推荐逻辑联动更新。 |
| tooltip | 自定义 `ToolTip` | `QWidget.setToolTip()` | 全局直接走 Qt 原生 tooltip。 |

### 3. 结果与详情卡

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 页面屏蔽规则按钮 | `ttk.Button` | `QPushButton` | 打开规则编辑对话框。 |
| 敏感过滤按钮 | `ttk.Button` | `QPushButton` | 打开过滤设置对话框。 |
| 全选/不全选按钮 | `ttk.Button` | `QPushButton` | 文案由选中数量驱动。 |
| 页面排序按钮 | `ttk.Button` | `QPushButton` | 调用 ViewModel 的页面平均分排序逻辑。 |
| 页面规则摘要 | `tk.Label` | `QLabel` | 只读摘要。 |
| 上下文选择摘要 | `tk.Label` | `QLabel` | 只读摘要。 |
| 结果 / 详情分隔 | `ttk.Panedwindow(orient='vertical')` | `QSplitter(Qt.Vertical)` | 需要单独持久化。 |

### 4. 结果表格

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 结果表本体 | `ttk.Treeview(show='headings')` | `QTableView` | 必须改为 Model/View。 |
| 结果表数据源 | 直接向 `Treeview` 插入行 | `QAbstractTableModel` | 统一承载命中行、选中态、排序态。 |
| 列排序 | `heading(command=...)` | `QHeaderView.sectionClicked` + model/proxy sort | 普通列排序与页面排序要分离。 |
| 首列选中标记 | 字符串 `[x]/[ ]` | `QStyledItemDelegate` 或 check-state 列 | 建议改为真正的可勾选列。 |
| 行选择 | `<<TreeviewSelect>>` | `QItemSelectionModel` | 选中后刷新预览。 |
| 垂直滚动 | `ttk.Scrollbar` | `QTableView` 内建滚动条 | 交给 Qt 视图系统。 |
| 表头样式 | `App.Treeview.Heading` | `QHeaderView + QSS` | 保持轻量主题即可。 |

### 5. 页面排序与表格排序

| 旧 UI 行为 | 当前 Tk 实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 普通列排序 | 原地 `list.sort(...)` | Model 层排序或 `QSortFilterProxyModel` | 推荐普通列走 proxy。 |
| 页面平均分排序 | 自定义 `_apply_page_sort()` | ViewModel 自定义排序命令 | 不建议硬塞进普通 proxy 规则，单独做一层结果排序策略。 |
| 恢复原排序 | 保存原 `chunk_id` 顺序 | ViewModel 保存原始顺序快照 | 保持与旧版一致。 |
| 选中行保持 | 手动记住 `chunk_id` | `QPersistentModelIndex` 或按 `chunk_id` 回查 | 排序后仍定位原行。 |

### 6. 详情页签区

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 三个详情页签 | `ttk.Notebook` | `QTabWidget` | 页签切换不重建内容。 |
| 片段详情文本区 | `tk.Text(readonly)` | `QPlainTextEdit(readOnly=True)` | 纯文本性能更好。 |
| 完整上下文文本区 | `tk.Text(readonly)` | `QPlainTextEdit(readOnly=True)` | 需要支持滚动定位与搜索高亮。 |
| 活动日志文本区 | `tk.Text(readonly)` | `QPlainTextEdit(readOnly=True)` | 适合持续 `appendPlainText`。 |
| 文本滚动条 | `ttk.Scrollbar` | `QPlainTextEdit` 内建滚动条 | 无需手工接线。 |
| 文本搜索状态 | `tk.Label` | `QLabel` | 放在文本页脚。 |
| 文本搜索输入 | `ttk.Entry` | `QLineEdit` | `returnPressed` 触发查找。 |
| 查找按钮 | `ttk.Button` | `QPushButton` | 调用当前页文本搜索。 |
| 下一个按钮 | `ttk.Button` | `QPushButton` | 跳转到下一命中。 |
| 搜索高亮 | `Text.tag_add/remove` | `QTextEdit.ExtraSelection` | 统一实现三页文本查找。 |

### 7. 完整上下文顶部跳转区

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 页面跳转下拉框 | `ttk.Combobox(readonly)` | `QComboBox` | 选项来自上下文章节解析。 |
| 页面摘要标签 | `tk.Label` | `QLabel` | 显示笔记页数量与片段数量。 |
| 跳转动作 | `Text.see(index)` | `QPlainTextEdit.setTextCursor()` | 按目标行定位。 |

---

## 三、配置页映射

### 1. 配置页骨架

| 旧 UI 位置 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 配置页主容器 | `Frame` | `QWidget` | 作为顶层第二个页签。 |
| 工作区标题区 | `Frame + Label + Button` | `QWidget + QHBoxLayout` | 右上保留帮助与更新入口。 |
| 五个子页签 | `ttk.Notebook` | `QTabWidget` | 每个页签内容包裹进 `QScrollArea`。 |
| 可滚动页签内容 | `Canvas + inner Frame + Scrollbar` | `QScrollArea + QWidget` | Qt 原生滚动容器即可。 |

### 2. 开始页

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 新手指引卡 | `Frame` | `QFrame` | 顶部说明区。 |
| 展开/收起按钮 | `ttk.Button` | `QPushButton` | 改为局部显隐，不销毁页面。 |
| 步骤编号徽标 | `tk.Label` | `QLabel` | 可做成圆角 badge。 |
| 步骤说明 | `tk.Label` | `QLabel` | 自动换行。 |
| 三个状态芯片 | `tk.Label` | `QLabel` 或自定义 `StatusChip` | 独立封装，支持 ok/warn/neutral 状态。 |
| 已保存笔记库下拉框 | `ttk.Combobox` | `QComboBox` | 只读模式。 |
| 删除已保存笔记库按钮 | `ttk.Button` | `QPushButton` | 触发删除当前项。 |
| 笔记库路径输入 | `ttk.Entry` | `QLineEdit` | 与浏览按钮组合。 |
| 数据目录输入 | `ttk.Entry` | `QLineEdit` | 与浏览按钮组合。 |
| 浏览按钮 | `ttk.Button` | `QPushButton` | 使用 `QFileDialog.getExistingDirectory()`。 |
| 预检 / 下载模型 / 重建 / 监听按钮 | `ttk.Button` | `QPushButton` | 统一命令面板。 |
| 统计卡片 | `Frame + Label` | `QFrame + QLabel` | 可复用小型 `StatCard` 组件。 |
| 预检摘要 / 监听摘要 | `tk.Label` | `QLabel` | 自动换行展示。 |
| 任务进度条 | `ttk.Progressbar` | `QProgressBar` | 支持确定/不确定两种模式。 |
| 暂停 / 继续 / 取消按钮 | `ttk.Button` | `QPushButton` | 仅重建任务时显示。 |
| 任务状态文案 | `tk.Label` | `QLabel` | 通过任务 ViewModel 更新。 |

### 3. 设置页

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 设备摘要 | `tk.Label` | `QLabel` | 自动换行。 |
| 向量后端 | `ttk.Combobox` | `QComboBox` | `lancedb / disabled`。 |
| 模型名输入 | `ttk.Entry` | `QLineEdit` | 直接绑定配置字段。 |
| 运行时 | `ttk.Combobox` | `QComboBox` | `torch / onnx`。 |
| 设备 | `ttk.Combobox` | `QComboBox` | 由硬件能力动态刷新选项。 |
| 监听间隔 | `ttk.Entry` | `QLineEdit` 或 `QDoubleSpinBox` | 推荐仍用 `QLineEdit + 校验器`。 |
| 建库资源档位 | `ttk.Combobox` | `QComboBox` | `quiet / balanced / peak`。 |
| 推荐配置按钮 | `ttk.Button` | `QPushButton` | 一次性回填多项配置。 |
| 加载配置按钮 | `ttk.Button` | `QPushButton` | 从当前数据目录加载。 |
| 保存配置按钮 | `ttk.Button` | `QPushButton` | 持久化配置。 |
| 高级选项展开按钮 | `ttk.Button` | `QPushButton` | 仅控制区域显隐。 |
| `仅本地模型` | `ttk.Checkbutton` | `QCheckBox` | 布尔配置。 |
| `强制继续` | `ttk.Checkbutton` | `QCheckBox` | 布尔配置。 |
| `强制轮询监听` | `ttk.Checkbutton` | `QCheckBox` | 布尔配置。 |
| 刷新按钮 | `ttk.Button` | `QPushButton` | 刷新当前状态。 |

### 4. UI 页

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 缩放输入 | `ttk.Entry` | `QLineEdit` 或 `QSpinBox` | 推荐 `QSpinBox`，但需兼容旧配置范围 `80-200`。 |
| 主题下拉 | `ttk.Combobox` | `QComboBox` | `system / light / dark`。 |
| 缩放说明 | `tk.Label` | `QLabel` | 保留提示文案。 |
| 应用 UI 按钮 | `ttk.Button` | `QPushButton` | 触发主题与缩放即时应用。 |

### 5. 检索强化页

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| reranker 状态摘要 | `tk.Label` | `QLabel` | 根据本地状态/快照刷新。 |
| 启用 reranker | `ttk.Checkbutton` | `QCheckBox` | 布尔开关。 |
| AI 协作导出 | `ttk.Checkbutton` | `QCheckBox` | 驱动上下文导出模式。 |
| reranker 模型输入 | `ttk.Entry` | `QLineEdit` | 模型标识字符串。 |
| CPU/CUDA 批量输入 | `ttk.Entry` | `QLineEdit` 或 `QSpinBox` | 推荐 `QLineEdit + 校验器` 保持兼容。 |
| 下载 reranker | `ttk.Button` | `QPushButton` | 走后台任务。 |
| 刷新状态 | `ttk.Button` | `QPushButton` | 重新读取状态快照。 |

### 6. 数据页

| 旧 UI 控件 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 当前工作区摘要 | `tk.Label` | `QLabel` | 自动换行。 |
| 打开笔记库目录 | `ttk.Button` | `QPushButton` | 通过 `QDesktopServices.openUrl(...)` 打开。 |
| 打开工作区目录 | `ttk.Button` | `QPushButton` | 同上。 |
| 打开导出目录 | `ttk.Button` | `QPushButton` | 同上。 |
| 清理勾选项 | `ttk.Checkbutton` | `QCheckBox` | 索引、日志、缓存、导出四类。 |
| 执行清理按钮 | `ttk.Button` | `QPushButton` | 触发带确认的后台清理。 |

---

## 四、弹窗与对话框映射

| 旧 UI 场景 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 页面屏蔽规则窗口 | `tk.Toplevel` | `QDialog` | 建议模态或应用内独立对话框。 |
| 规则滚动容器 | `Canvas + inner Frame` | `QTableView` 或 `QScrollArea` | 推荐 `QTableView + 自定义 model` 管理规则行。 |
| 规则启用勾选 | `ttk.Checkbutton` | `QCheckBox` delegate | 放入规则表模型。 |
| 正则输入 | `ttk.Entry` | `QLineEdit` delegate | 放入规则表模型。 |
| 删除规则按钮 | `ttk.Button` | `QPushButton` delegate 或外部按钮 | 若用表格模式，可加操作列 delegate。 |
| 新增规则按钮 | `ttk.Button` | `QPushButton` | 增加一行规则。 |
| 恢复默认规则按钮 | `ttk.Button` | `QPushButton` | 回填默认规则。 |
| 保存规则按钮 | `ttk.Button` | `QPushButton` | 保存后刷新查询。 |
| 敏感过滤窗口 | `tk.Toplevel` | `QDialog` | 建议独立对话框。 |
| 核心 / 扩展开关 | `ttk.Checkbutton` | `QCheckBox` | 布尔配置。 |
| 自定义规则说明 | `tk.Label` | `QLabel` | 自动换行。 |
| 自定义规则输入 | `tk.Text` | `QPlainTextEdit` | 更适合多行纯文本编辑。 |
| 各类确认 / 信息 / 错误框 | `messagebox.*` | `QMessageBox` | 统一封装为 dialog service。 |
| 目录选择 | `filedialog.askdirectory` | `QFileDialog.getExistingDirectory()` | 保持选择目录语义。 |

---

## 五、状态、变量与异步模型映射

| 旧 UI 机制 | Tk 旧实现 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| `StringVar / BooleanVar` | Tk 变量系统 | `QObject` 属性 + signal 或 dataclass ViewModel | 不再让控件直接拥有业务状态。 |
| UI 刷新 | 手动更新控件文本/状态 | Signal 驱动局部刷新 | 减少整页重绘。 |
| 任务队列 | `queue.Queue + root.after` 轮询 | `QThread` / `QThreadPool` + signals/slots | 去掉主线程轮询驱动。 |
| 任务耗时刷新 | `after(500)` | `QTimer` | 只保留耗时/时钟类轻量定时器。 |
| 查询状态条 | 读 `busy / active_task_key / latest_task_progress` | 查询状态 ViewModel + signals | 颜色、标题、详情统一由状态对象驱动。 |
| 活动日志 | `list[str]` + 文本追加 | `QStringListModel` 或日志 ViewModel | UI 文本区只负责展示。 |
| 结果数据 | `current_hits` 列表 | `ResultsTableModel` | 单一真实来源。 |
| 选中片段集合 | `selected_chunk_ids` | ViewModel 中的 `set[str]` | 表格首列与上下文重建共享同一状态。 |
| 当前工作区摘要 | `StringVar` | `QLabel` + ViewModel 字段 | 只读派生状态。 |

---

## 六、持久化与布局记忆映射

| 旧 UI 持久化项 | Tk 当前方式 | Qt 目标实现 | 迁移备注 |
| --- | --- | --- | --- |
| 窗口几何 | `ui_window_geometry` 字符串 | `QMainWindow.saveGeometry()/restoreGeometry()` | 新旧并存期建议增加独立 Qt 字段，避免互相污染。 |
| 查询页上下分隔 | `ui_right_sash` 整数 | `QSplitter.saveState()/restoreState()` | 建议新增 `qt_query_splitter_state`。 |
| 结果/详情分隔 | `ui_results_sash` 整数 | `QSplitter.saveState()/restoreState()` | 建议新增 `qt_results_splitter_state`。 |
| 顶层 / 子页签索引 | 运行时保留 | 可选持久化到 Qt 专属字段 | 旧版当前不持久化，可保持不持久化。 |
| 主题 | `ui_theme` | 继续复用 `ui_theme` | 与旧版共享字段没问题。 |
| 缩放 | `ui_scale_percent` | 继续复用 `ui_scale_percent` | 保持统一配置体验。 |
| 语言 | `ui_language` | 继续复用 `ui_language` | 新旧 UI 共享字段。 |
| 新手指引展开态 | `ui_quick_start_expanded` | 继续复用该字段 | 保持体验一致。 |
| 高级选项展开态 | 未持久化 | 继续不持久化或新增 Qt 字段 | 默认建议保持不持久化。 |

---

## 七、推荐的 Qt 组件拆分

| Qt 组件 | 负责内容 | 对应旧 UI |
| --- | --- | --- |
| `MainWindow` | 根窗口、顶栏、主页签、状态栏 | 顶层壳层 |
| `QueryWorkspace` | 搜索卡、结果卡、状态条 | 查询页 |
| `ResultsTableModel` | 命中列表、勾选态、列值、普通排序 | `Treeview` 结果表 |
| `QueryDetailTabs` | 片段详情、完整上下文、活动日志 | 三个详情页签 |
| `ContextPackViewModel` | 上下文重建、页面跳转项、复制文本 | 完整上下文页 |
| `WorkspaceConfigPage` | `开始 / 设置 / UI / 检索强化 / 数据` 子页签 | 配置页 |
| `TaskPanel` | 任务进度、暂停、继续、取消 | 开始页任务面板 |
| `PageBlocklistDialog` | 页面屏蔽规则管理 | 页面规则弹窗 |
| `SensitiveFilterDialog` | 敏感过滤设置 | 敏感过滤弹窗 |

---

## 八、迁移时必须保持的工程约束

- 所有重任务必须通过 `QThread` 或 `QThreadPool` 与 UI 主线程隔离。
- 所有大数据列表必须使用 Qt `Model/View`，不能回退成一堆手工子控件。
- 所有文本说明类控件优先使用 Qt 自动换行，不再复制 Tk 的手工 `wraplength` 计算。
- 所有布局记忆优先使用 Qt 原生 `saveState/restoreState` 和 `saveGeometry/restoreGeometry`。
- 新旧 UI 并存期间，Qt 的布局状态建议写入独立配置字段，避免破坏旧版 Tk 的读取逻辑。
- 新版 UI 默认入口可以切换回旧版 UI，但旧版入口不能依赖新版控件层才能启动。
