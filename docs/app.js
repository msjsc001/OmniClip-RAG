(() => {
  const STORAGE_KEY = "caelune-site-language";
  const LEGACY_STORAGE_KEY = "omniclip-site-language";
  const HTML_KEYS = new Set(["hero_title"]);
  const translations = {
    en: {
      page_title: "Caelune | Private Knowledge, Local Retrieval",
      page_description: "Search private Markdown, PDF, and Tika-backed documents locally on Windows. Use hybrid retrieval in the desktop app or connect AI clients through a read-only MCP server.",
      skip: "Skip to main content",
      nav_features: "Features",
      nav_workflow: "Workflow",
      nav_interface: "Interface",
      nav_download: "Download",
      hero_eyebrow: "Windows · Local-first · Read-only MCP",
      hero_title: "Private knowledge.<br>Local retrieval.",
      hero_lead: "Search Markdown, PDF, and Tika-supported documents with hybrid lexical and semantic retrieval—without uploading your knowledge base.",
      hero_scroll_hint: "Explore the system below",
      download_windows: "Download Windows (WIN-EXE)",
      view_source: "View source",
      fact_local: "Indexing and queries stay on this device",
      fact_models: "CPU and NVIDIA CUDA supported",
      fact_license: "Open source under the MIT License",
      runtime_ready: "Runtime ready",
      tab_query: "Query",
      tab_results: "Results",
      tab_config: "Config",
      tab_log: "Activity log",
      query_label: "QUERY DESK",
      query_question: "What matters most in these notes?",
      stage_rerank: "Reranking · 4/6",
      query_placeholder: "Search your local knowledge base",
      search_button: "Search",
      hybrid_cuda: "Hybrid · CUDA",
      result_title: "Decision principles",
      result_excerpt: "The best match is grounded in the original note and keeps its source path.",
      signal_formats: "One search surface",
      signal_hybrid: "Hybrid retrieval",
      signal_models: "Local embedding and reranking",
      signal_privacy: "No usage tracking",
      features_kicker: "WHAT IT ACTUALLY DOES",
      features_title: "Turn scattered notes into a local index you can actually search.",
      features_intro: "Exact matches, semantic recall, and source paths meet in one result list you can inspect.",
      feature_hybrid_title: "Hybrid retrieval",
      feature_hybrid_body: "Exact FTS5 matches and LanceDB semantic candidates are fused, filtered, and optionally reranked.",
      feature_watch_title: "Calm incremental watch",
      feature_watch_body: "File changes are collected and updated in batches. When nothing changes, the watcher stays light.",
      feature_control_title: "You control the context",
      feature_control_body: "Inspect sources, select results, filter sensitive pages, and copy only the evidence you want.",
      feature_formats_title: "Beyond Markdown",
      feature_formats_body: "PDF and Apache Tika-backed formats use isolated indexes and background tasks that do not block the interface.",
      feature_runtime_title: "Resource-aware runtime",
      feature_runtime_body: "Auto mode can use NVIDIA CUDA, recover safely, and explain when a stage falls back or needs attention.",
      feature_mcp_title: "Read-only MCP",
      feature_mcp_body: "Let compatible AI clients search the same local index through two focused, read-only tools.",
      workflow_kicker: "FROM FOLDER TO RESULT",
      workflow_title: "Choose a folder. Let it index. Start asking.",
      workflow_intro: "Keep your folders and writing habits. Caelune works around them.",
      workflow_visual_caption: "Search local Markdown, PDF, and other documents, review source-linked results directly, or provide selected evidence to AI.",
      workflow_one_title: "Choose your folders",
      workflow_one_body: "Add a Markdown vault and optional PDF or Tika directories. Each workspace stays isolated.",
      workflow_two_title: "Build the local index",
      workflow_two_body: "Create lexical and semantic indexes locally, with visible progress and resumable work.",
      workflow_three_title: "Retrieve grounded context",
      workflow_three_body: "Search in the desktop app, copy selected context, or expose results through read-only MCP.",
      boundary_kicker: "CLEAR PRIVACY BOUNDARY",
      boundary_title: "Local by default, explicit when data moves.",
      boundary_body: "Indexing and search run on your machine. Model and Runtime downloads occur only when you start installation. Context leaves only when you copy it or an MCP client requests a result.",
      boundary_stays: "Stays local",
      boundary_stays_body: "Source files, indexes, embeddings, configuration, and query execution.",
      boundary_moves: "Moves only by action",
      boundary_moves_body: "Selected clipboard context or read-only MCP search results.",
      boundary_network: "Network is optional",
      boundary_network_body: "Used for user-initiated downloads of the app, Runtime, or models—not for telemetry.",
      mcp_kicker: "READ-ONLY MCP BRIDGE",
      mcp_title: "One local index, available to compatible AI clients.",
      mcp_body: "Caelune exposes status and search through a narrow stdio surface. It does not provide write, delete, or file-editing tools.",
      mcp_setup: "Read MCP setup",
      mcp_registry: "Open MCP Registry",
      mcp_ready: "ready · hybrid",
      mcp_results: "15 sourced results",
      mcp_note: "Your client receives selected evidence. Your archive remains local.",
      interface_kicker: "DESKTOP INTERFACE",
      interface_title: "See the work behind every result.",
      interface_intro: "Stage status, source paths, and match reasons stay visible from query to context.",
      real_interface_badge: "PRODUCT INTERFACE",
      real_interface_title: "Query Console and Results and Details",
      real_interface_body: "Ask in natural language, follow each retrieval stage, and review the evidence before preparing context.",
      live_query_kicker: "QUERY CONSOLE",
      live_query_title: "Search your local knowledge base",
      live_status_reranking: "Reranking",
      live_min_relevance: "Min relevance",
      live_result_count: "Result count",
      live_query_sources: "Search sources",
      live_stage_lexical: "Lexical",
      live_stage_semantic: "Semantic",
      live_stage_fusion: "Fusion",
      live_stage_rerank: "Rerank",
      live_stage_context: "Context",
      live_results_kicker: "RESULTS AND DETAILS",
      live_results_title: "Review evidence before using it",
      live_filter_titles: "Filter titles",
      live_filter_sensitive: "Sensitive content",
      live_col_page: "Page",
      live_col_reason: "Why it matched",
      live_col_relevance: "Relevance",
      live_snippet_label: "SNIPPET DETAILS",
      live_snippet_title: "Evidence remains connected to its source.",
      live_snippet_body: "Select a result to inspect the matching passage and prepare only the context you want to use.",
      interface_swipe_hint: "Swipe the cards to see more interface states.",
      interface_query_label: "QUERY",
      interface_query_title: "Know what the search is doing",
      stage_semantic: "Semantic recall · 3/6",
      demo_query: "How did I define opportunity cost?",
      demo_result_one: "Decision journal / Opportunity cost",
      demo_result_one_body: "Matched by semantic meaning and source structure.",
      demo_result_two: "Notes / Trade-offs",
      demo_result_two_body: "Related context from a different source.",
      interface_start_label: "START",
      interface_start_title: "Runtime and index status",
      status_runtime: "Runtime ready",
      status_detected: "Detected",
      status_index: "Semantic index",
      status_ready: "Ready",
      status_watch: "Incremental watch",
      status_waiting: "Waiting for changes",
      interface_formats_label: "EXTENSIONS",
      interface_formats_title: "Isolated format pipelines",
      format_background: "Background indexing",
      format_documents: "Tika-supported documents",
      download_kicker: "GET STARTED",
      download_title: "Your knowledge base stays yours.",
      download_body: "Download the Windows build, choose a folder, install the local Runtime, and build your first index.",
      read_docs: "Read installation guide",
      footer_tagline: "Private knowledge. Local retrieval.",
      footer_releases: "Releases"
    },
    zh: {
      page_title: "Caelune | 私人知识，本地检索",
      page_description: "在 Windows 本地检索私人 Markdown、PDF 与 Tika 文档。通过桌面端使用混合检索，或用只读 MCP 连接兼容的 AI 客户端。",
      skip: "跳到主要内容",
      nav_features: "核心功能",
      nav_workflow: "使用流程",
      nav_interface: "软件界面",
      nav_download: "下载",
      hero_eyebrow: "Windows · 本地优先 · 只读 MCP",
      hero_title: "私人知识。<br>本地检索。",
      hero_lead: "在本机混合检索 Markdown、PDF 与 Tika 支持的文档，无需上传你的知识库。",
      hero_scroll_hint: "继续向下了解",
      download_windows: "下载 Windows 版（WIN-EXE）",
      view_source: "查看源代码",
      fact_local: "索引和查询都在当前设备完成",
      fact_models: "支持 CPU 与 NVIDIA CUDA",
      fact_license: "MIT 许可的开源软件",
      runtime_ready: "Runtime 已就绪",
      tab_query: "查询",
      tab_results: "结果与详情",
      tab_config: "配置",
      tab_log: "活动日志",
      query_label: "查询台",
      query_question: "这些笔记里最重要的是什么？",
      stage_rerank: "正在重排 · 4/6",
      query_placeholder: "搜索本地知识库",
      search_button: "查询",
      hybrid_cuda: "混合检索 · CUDA",
      result_title: "决策原则",
      result_excerpt: "结果来自原始笔记，并保留可追溯的来源路径。",
      signal_formats: "一个查询入口",
      signal_hybrid: "混合检索",
      signal_models: "本地向量与重排",
      signal_privacy: "不收集使用数据",
      features_kicker: "它实际做什么",
      features_title: "把散落的笔记，变成随时可查的本地索引。",
      features_intro: "精确匹配、语义召回和来源路径，最后汇成一份可以核对的结果。",
      feature_hybrid_title: "混合检索",
      feature_hybrid_body: "融合 FTS5 精确匹配与 LanceDB 语义候选，再进行过滤和可选的二次重排。",
      feature_watch_title: "克制的增量监听",
      feature_watch_body: "文件变化会先被收集，再按间隔批量更新；没有变化时保持轻量空闲。",
      feature_control_title: "上下文由你控制",
      feature_control_body: "检查来源、选择结果、过滤敏感页面，只复制你真正需要的证据。",
      feature_formats_title: "不止 Markdown",
      feature_formats_body: "PDF 与 Apache Tika 格式使用隔离索引和后台任务，不拖慢主界面。",
      feature_runtime_title: "理解资源的 Runtime",
      feature_runtime_body: "Auto 可使用 NVIDIA CUDA、安全恢复，并说明每个阶段的回退或修复条件。",
      feature_mcp_title: "只读 MCP",
      feature_mcp_body: "兼容的 AI 客户端可通过两个专注的只读工具搜索同一个本地索引。",
      workflow_kicker: "从目录到结果",
      workflow_title: "选目录，等索引，然后直接问。",
      workflow_intro: "目录结构和写作习惯照旧，Caelune 只负责把内容变得可查。",
      workflow_visual_caption: "在本地查询 Markdown、PDF 和其他文档，直接查看带来源的结果，或将选定证据提供给 AI。",
      workflow_one_title: "选择知识目录",
      workflow_one_body: "添加 Markdown 知识库，并按需加入 PDF 或 Tika 目录；不同工作区彼此隔离。",
      workflow_two_title: "建立本地索引",
      workflow_two_body: "在本机建立字面与语义索引，过程可见，并支持继续未完成的任务。",
      workflow_three_title: "提取有根据的上下文",
      workflow_three_body: "在桌面端查询、复制选定上下文，或通过只读 MCP 返回检索结果。",
      boundary_kicker: "清楚的隐私边界",
      boundary_title: "默认留在本地，移动必须明确。",
      boundary_body: "索引与查询都在你的电脑执行。只有当你主动安装时才下载 Runtime 或模型；只有复制内容或 MCP 请求结果时，上下文才会离开程序。",
      boundary_stays: "始终留在本机",
      boundary_stays_body: "源文件、索引、向量、配置与查询执行过程。",
      boundary_moves: "主动操作才移动",
      boundary_moves_body: "你选择的剪贴板上下文，或只读 MCP 返回的搜索结果。",
      boundary_network: "联网不是常态",
      boundary_network_body: "仅用于用户主动下载软件、Runtime 或模型，不用于遥测。",
      mcp_kicker: "只读 MCP 桥接",
      mcp_title: "一个本地索引，供兼容的 AI 客户端调用。",
      mcp_body: "Caelune 通过精简的 stdio 接口提供状态和搜索，不提供写入、删除或修改文件的工具。",
      mcp_setup: "阅读 MCP 接入指南",
      mcp_registry: "打开 MCP Registry",
      mcp_ready: "已就绪 · 混合模式",
      mcp_results: "15 条带来源结果",
      mcp_note: "客户端得到选定证据，完整知识库继续留在本地。",
      interface_kicker: "桌面端界面",
      interface_title: "查询做到哪一步，为什么命中，都摆在明面上。",
      interface_intro: "从查询到上下文，阶段状态、来源路径和命中原因始终清楚可查。",
      real_interface_badge: "软件界面",
      real_interface_title: "查询台与结果和详情",
      real_interface_body: "输入自然语言问题，查看每个检索阶段，并在准备上下文前核对命中依据。",
      live_query_kicker: "查询台",
      live_query_title: "查询本地知识库",
      live_status_reranking: "正在重排",
      live_min_relevance: "最低相关性",
      live_result_count: "查询条数",
      live_query_sources: "查询来源",
      live_stage_lexical: "字面召回",
      live_stage_semantic: "语义召回",
      live_stage_fusion: "融合排序",
      live_stage_rerank: "重排",
      live_stage_context: "组装上下文",
      live_results_kicker: "结果与详情",
      live_results_title: "使用前检查检索依据",
      live_filter_titles: "过滤页面标题",
      live_filter_sensitive: "过滤敏感内容",
      live_col_page: "页面",
      live_col_reason: "命中原因",
      live_col_relevance: "相关性",
      live_snippet_label: "片段详情",
      live_snippet_title: "检索依据始终与来源相连。",
      live_snippet_body: "选择一条结果即可查看命中片段，并只准备你希望使用的上下文。",
      interface_swipe_hint: "左右滑动卡片，可查看更多界面状态。",
      interface_query_label: "查询",
      interface_query_title: "清楚知道查询正在做什么",
      stage_semantic: "语义召回 · 3/6",
      demo_query: "我是怎样理解机会成本的？",
      demo_result_one: "决策日志 / 机会成本",
      demo_result_one_body: "根据语义含义与来源结构匹配。",
      demo_result_two: "笔记 / 权衡取舍",
      demo_result_two_body: "来自其他来源的相关上下文。",
      interface_start_label: "开始",
      interface_start_title: "Runtime 与索引状态",
      status_runtime: "Runtime",
      status_detected: "检测通过",
      status_index: "语义索引",
      status_ready: "已就绪",
      status_watch: "增量监听",
      status_waiting: "等待文件变化",
      interface_formats_label: "拓展格式",
      interface_formats_title: "彼此隔离的格式管线",
      format_background: "后台建立索引",
      format_documents: "Tika 支持的文档格式",
      download_kicker: "开始使用",
      download_title: "让知识库继续属于你。",
      download_body: "下载 Windows 版本，选择目录，安装本地 Runtime，然后建立第一个索引。",
      read_docs: "阅读安装说明",
      footer_tagline: "私人知识，本地检索。",
      footer_releases: "版本下载"
    }
  };

  const languageButtons = [...document.querySelectorAll("[data-language]")];
  const textNodes = [...document.querySelectorAll("[data-i18n]")];
  const ariaNodes = [...document.querySelectorAll("[data-aria-zh]")];
  const altNodes = [...document.querySelectorAll("[data-alt-zh]")];
  const descriptionMeta = document.querySelector('meta[name="description"]');
  const ogTitle = document.querySelector('meta[property="og:title"]');
  const ogDescription = document.querySelector('meta[property="og:description"]');
  const twitterTitle = document.querySelector('meta[name="twitter:title"]');
  const twitterDescription = document.querySelector('meta[name="twitter:description"]');

  function storedLanguage() {
    try {
      return window.localStorage.getItem(STORAGE_KEY)
        || window.localStorage.getItem(LEGACY_STORAGE_KEY);
    } catch (_error) {
      return null;
    }
  }

  function saveLanguage(language) {
    try {
      window.localStorage.setItem(STORAGE_KEY, language);
    } catch (_error) {
      // A disabled storage surface should not block the language switch.
    }
  }

  function preferredLanguage() {
    const stored = storedLanguage();
    if (stored === "en" || stored === "zh") {
      return stored;
    }
    return String(navigator.language || "").toLowerCase().startsWith("zh") ? "zh" : "en";
  }

  function applyLanguage(language) {
    const nextLanguage = language === "zh" ? "zh" : "en";
    const dictionary = translations[nextLanguage];
    document.documentElement.lang = nextLanguage;
    document.body.dataset.language = nextLanguage;
    document.title = dictionary.page_title;

    textNodes.forEach((node) => {
      const key = node.dataset.i18n;
      const value = dictionary[key];
      if (typeof value !== "string") {
        return;
      }
      if (HTML_KEYS.has(key)) {
        node.innerHTML = value;
      } else {
        node.textContent = value;
      }
    });

    ariaNodes.forEach((node) => {
      if (!node.dataset.ariaEn) {
        node.dataset.ariaEn = node.getAttribute("aria-label") || "";
      }
      node.setAttribute("aria-label", nextLanguage === "zh" ? node.dataset.ariaZh : node.dataset.ariaEn);
    });

    altNodes.forEach((node) => {
      if (!node.dataset.altEn) {
        node.dataset.altEn = node.getAttribute("alt") || "";
      }
      node.setAttribute("alt", nextLanguage === "zh" ? node.dataset.altZh : node.dataset.altEn);
    });

    languageButtons.forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.language === nextLanguage));
    });

    if (descriptionMeta) descriptionMeta.content = dictionary.page_description;
    if (ogTitle) ogTitle.content = dictionary.page_title;
    if (ogDescription) ogDescription.content = dictionary.page_description;
    if (twitterTitle) twitterTitle.content = dictionary.page_title;
    if (twitterDescription) twitterDescription.content = dictionary.page_description;
    saveLanguage(nextLanguage);
  }

  languageButtons.forEach((button) => {
    button.addEventListener("click", () => applyLanguage(button.dataset.language));
  });

  const siteHeader = document.querySelector(".site-header");
  const pageContent = document.querySelector(".page-content");
  const heroMotionRoot = document.querySelector(".hero-stage .hero");
  const reducedMotionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
  let headerFramePending = false;

  function syncHeaderTheme() {
    headerFramePending = false;
    if (!siteHeader || !pageContent) return;
    const contentTop = pageContent.getBoundingClientRect().top;
    const contentHasReachedHeader = contentTop <= siteHeader.offsetHeight + 12;
    siteHeader.classList.toggle("is-past-hero", contentHasReachedHeader);

    if (!heroMotionRoot) return;
    const progress = Math.max(0, Math.min(1, 1 - contentTop / window.innerHeight));
    const viewportFactor = window.innerWidth <= 700 ? 0.56 : 1;
    const accessibilityFactor = reducedMotionQuery.matches ? 0.42 : 1;
    const motion = progress * viewportFactor * accessibilityFactor;
    const baseScale = window.innerWidth <= 700 ? 1.02 : 1.012;

    heroMotionRoot.style.setProperty("--scene-x", `${(-4 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--scene-y", `${(-12 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--scene-scale", (baseScale + 0.018 * motion).toFixed(4));
    heroMotionRoot.style.setProperty("--stars-y", `${(-22 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--beam-x", `${(4 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--beam-y", `${(-7 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--beam-scale", (1 + 0.08 * motion).toFixed(4));
    heroMotionRoot.style.setProperty("--beam-opacity", (0.12 + 0.1 * motion).toFixed(3));
    heroMotionRoot.style.setProperty("--doc-one-x", `${(-10 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-one-y", `${(-28 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-one-rotation", `${(-5 + 1.2 * motion).toFixed(3)}deg`);
    heroMotionRoot.style.setProperty("--doc-two-x", `${(8 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-two-y", `${(-18 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-two-rotation", `${(6 - motion).toFixed(3)}deg`);
    heroMotionRoot.style.setProperty("--doc-three-x", `${(-6 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-three-y", `${(-20 * motion).toFixed(2)}px`);
    heroMotionRoot.style.setProperty("--doc-three-rotation", `${(-2 + 0.7 * motion).toFixed(3)}deg`);
  }

  function requestHeaderSync() {
    if (headerFramePending) return;
    headerFramePending = true;
    window.requestAnimationFrame(syncHeaderTheme);
  }

  window.addEventListener("scroll", requestHeaderSync, { passive: true });
  window.addEventListener("resize", requestHeaderSync);

  applyLanguage(preferredLanguage());
  syncHeaderTheme();
})();
