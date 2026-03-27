# Third-Party Notices / 第三方许可与声明

## Purpose / 文档目的

This file is the repository-level notice for the major third-party projects, runtimes, and optional model artifacts that OmniClip RAG directly integrates, explicitly relies on, or may download during normal use.

本文件是方寸引 / OmniClip RAG 在仓库层面的第三方许可与声明入口，用来记录本项目直接集成、明确依赖、或在正常使用过程中可能下载的主要第三方项目、运行时与模型。

This document is:

- a practical compliance notice for this repository and its official distribution line,
- not legal advice,
- not a substitute for the full upstream license texts, NOTICE files, model cards, or platform terms,
- not a complete transitive dependency bill of materials.

本文件是：

- 面向本仓库与官方发布线的实用合规说明，
- 不是法律意见，
- 不能替代上游完整许可证文本、NOTICE、模型卡或平台条款，
- 也不是完整的传递依赖清单。

When there is any conflict, ambiguity, or version mismatch, the authoritative upstream license text, NOTICE file, model card, or platform terms always take precedence.

如出现冲突、歧义或版本不一致，以上游官方许可证文本、NOTICE、模型卡与平台条款为准。

---

## Project License / 本项目许可证

OmniClip RAG itself is released under the [MIT License](LICENSE).

方寸引 / OmniClip RAG 自身采用 [MIT License](LICENSE) 发布。

That does **not** replace the licenses of third-party code, runtimes, models, or tools used by this project.

但这 **不替代** 本项目所使用的第三方代码、运行时、模型与工具各自的许可证。

---

## Important Distribution Notes / 重要分发说明

1. The official GUI and MCP release artifacts are not a single monolithic bundle of every possible dependency. Some heavy Runtime components and models are intentionally downloaded or installed later.
2. Some third-party components are bundled into the packaged application, while others are only used during build, testing, Runtime installation, model download, or optional extension workflows.
3. If you redistribute modified binaries, repackage official assets, or publish your own downstream distribution, you should additionally carry forward the upstream license texts and NOTICE files required by the components you actually bundle.
4. Model weights, model cards, and hosting platforms may impose their own terms in addition to the software licenses of the Python libraries that load them.

1. 官方 GUI 与 MCP 发布物并不是“把所有可能依赖一次性塞进一个巨包”的分发方式；部分重量级 Runtime 与模型会在后续按需下载或安装。
2. 有些第三方组件会进入打包后的应用，有些只在构建、测试、Runtime 安装、模型下载或扩展工作流中使用。
3. 如果你要再分发修改后的二进制、重打官方包，或做自己的下游发行版，除了本文件外，还应继续携带你实际打包进去的上游许可证文本与 NOTICE。
4. 模型权重、模型卡与托管平台可能还有独立条款，它们不等同于加载这些模型的 Python 库许可证。

---

## Representative Core Components / 代表性核心第三方组件

The table below is intentionally focused on the major projects that are directly visible in OmniClip's architecture, Runtime line, build line, or model workflows.

下表刻意聚焦在方寸引架构、Runtime、构建链与模型链里最核心、最可见的项目。

| Component | How OmniClip uses it | License / note | Upstream |
| --- | --- | --- | --- |
| Python | Core language/runtime for source tree, tooling, and bundled Runtime installer support | PSF License | <https://docs.python.org/3/license.html> |
| Qt for Python (`PySide6`, `Shiboken6`) | Desktop GUI framework | LGPL-based community distribution with additional upstream third-party notices; see Qt documentation for details | <https://doc.qt.io/qtforpython-6/licenses.html> |
| SQLite | Local metadata/index authority via Python stdlib | Public domain | <https://www.sqlite.org/copyright.html> |
| `pypdf` | PDF parsing in the extension pipeline | BSD-3-Clause | <https://pypi.org/project/pypdf/> |
| `watchdog` | File watching / hot reload support | Apache-2.0 | <https://github.com/gorakhargosh/watchdog> |
| PyInstaller | Windows packaging/build line | GPL-2.0-or-later with PyInstaller's special exception for bundled apps | <https://www.pyinstaller.org/> |
| MCP Python SDK (`mcp`) | MCP server implementation | MIT | <https://github.com/modelcontextprotocol/python-sdk> |
| LanceDB | Local vector store backend in Runtime | Apache-2.0 | <https://github.com/lancedb/lancedb> |
| PyTorch | Local embedding/reranking Runtime foundation | BSD-style license; see upstream LICENSE and NOTICE | <https://github.com/pytorch/pytorch> |
| `sentence-transformers` | Embedding / reranking model loading | Apache-2.0; upstream also ships a NOTICE file | <https://github.com/huggingface/sentence-transformers> |
| `transformers` | Local model/tokenizer loading | Apache-2.0 | <https://github.com/huggingface/transformers> |
| `huggingface_hub` | Model download/cache client | Apache-2.0 | <https://github.com/huggingface/huggingface_hub> |
| ONNX Runtime | Optional local inference backend and Runtime component | MIT | <https://github.com/microsoft/onnxruntime> |
| ModelScope | Optional China-friendly model download path | Apache-2.0 | <https://github.com/modelscope/modelscope> |
| Apache Tika | Optional extension parsing stack for broad document coverage | Apache-2.0 | <https://tika.apache.org/> |
| Eclipse Temurin / OpenJDK | Optional Java Runtime used by the Tika line | GPL-2.0 with Classpath Exception | <https://adoptium.net/> |

---

## Optional Downloaded Models / 可选下载模型

These model artifacts are not the same thing as the Python libraries that load them. Their model cards and hosting pages should be reviewed independently before redistribution or downstream commercial use.

这些模型文件不等同于加载它们的 Python 库；若要再分发或做下游商业使用，应单独查看模型卡与托管页条款。

| Model | Typical role in OmniClip | License shown by upstream model page | Upstream |
| --- | --- | --- | --- |
| `BAAI/bge-m3` | Local embedding model | MIT | <https://huggingface.co/BAAI/bge-m3> |
| `BAAI/bge-reranker-v2-m3` | Local reranker model | Apache-2.0 | <https://huggingface.co/BAAI/bge-reranker-v2-m3> |

---

## Practical Compliance Boundary / 实际合规边界

For this repository today, the practical baseline is:

- keep this notice file in the repository as the formal entry point,
- keep the README "Open Source Thanks" section as a human-readable summary rather than the legal source of truth,
- review upstream license/NOTICE changes when upgrading major dependencies, Runtime manifests, or model defaults,
- carry forward the relevant upstream license texts and NOTICE files when you create downstream packaged distributions.

对本仓库当前最现实的合规基线是：

- 用本文件作为仓库层面的正式第三方声明入口，
- 继续把 README 里的“开源致谢”保留为人类可读摘要，而不是法律真相源，
- 升级关键依赖、Runtime manifest 或默认模型时，顺手复核上游许可证与 NOTICE 变化，
- 如果要做自己的下游安装包或再分发，请继续把相关上游许可证文本与 NOTICE 一并带上。

---

## Last Updated / 最近更新

This notice was added during the `v0.4.7` documentation/compliance pass and is expected to evolve as the distribution line evolves.

本声明在 `v0.4.7` 之后的文档/合规补强阶段加入，后续会随着分发线变化持续更新。
