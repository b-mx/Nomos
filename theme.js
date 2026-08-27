// Manual light/dark/system theme override, persisted per-browser via
// localStorage. Falls back to the OS preference (the existing
// `prefers-color-scheme` CSS) until a visitor picks something else.
//
// This runs as an external script, never inline — this page's CSP has no
// 'unsafe-inline' — so there's an unavoidable one-frame flash of the
// system theme before a stored override applies. That's the accepted
// trade-off for keeping the CSP strict rather than relaxing it just to
// eliminate the flash.

const THEME_KEY = "nomos-theme";
const THEME_ORDER = ["system", "light", "dark"];
const THEME_ICONS = {
  system: "https://api.iconify.design/octicon/device-desktop-16.svg",
  light: "https://api.iconify.design/octicon/sun-16.svg",
  dark: "https://api.iconify.design/octicon/moon-16.svg",
};

function getStoredTheme() {
  try {
    return localStorage.getItem(THEME_KEY);
  } catch {
    return null;
  }
}

function setStoredTheme(theme) {
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage unavailable (private browsing, etc.) — theme just won't persist.
  }
}

function applyTheme(theme) {
  if (theme === "system") {
    delete document.documentElement.dataset.theme;
  } else {
    document.documentElement.dataset.theme = theme;
  }
  const icon = document.getElementById("theme-toggle-icon");
  if (icon) icon.src = THEME_ICONS[theme];
  const button = document.getElementById("theme-toggle");
  if (button) button.setAttribute("aria-label", `Color theme: ${theme}. Click to change.`);
}

function initThemeToggle() {
  let theme = getStoredTheme() || "system";
  applyTheme(theme);

  const button = document.getElementById("theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    theme = THEME_ORDER[(THEME_ORDER.indexOf(theme) + 1) % THEME_ORDER.length];
    setStoredTheme(theme);
    applyTheme(theme);
  });
}

initThemeToggle();
