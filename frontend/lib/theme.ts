export const themeKey = 'kontur-theme';

export const initializeTheme = `try {
  const preference = localStorage.getItem(${JSON.stringify(themeKey)});
  if (preference === 'light' || preference === 'dark') {
    document.documentElement.dataset.theme = preference;
  }
} catch {}`;
