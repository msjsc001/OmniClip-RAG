(function () {
  const switches = document.querySelectorAll("[data-lang-switch]");
  const localizedNodes = document.querySelectorAll("[data-zh]");
  const storageKey = "omniclip-site-lang";
  const supported = new Set(["en", "zh"]);

  function applyLanguage(lang) {
    const nextLang = supported.has(lang) ? lang : "en";
    document.documentElement.lang = nextLang;
    document.body.dataset.lang = nextLang;

    localizedNodes.forEach((node) => {
      const zhText = node.getAttribute("data-zh");
      const enText = node.getAttribute("data-en") || node.textContent;
      if (!node.dataset.enInitialized) {
        node.dataset.en = enText;
        node.dataset.enInitialized = "true";
      }
      node.textContent = nextLang === "zh" ? zhText : node.dataset.en;
    });

    switches.forEach((button) => {
      const active = button.getAttribute("data-lang-switch") === nextLang;
      button.classList.toggle("is-active", active);
      button.setAttribute("aria-pressed", String(active));
    });

    localStorage.setItem(storageKey, nextLang);
  }

  switches.forEach((button) => {
    button.addEventListener("click", () => {
      applyLanguage(button.getAttribute("data-lang-switch"));
    });
  });

  applyLanguage(localStorage.getItem(storageKey) || "en");
}());
