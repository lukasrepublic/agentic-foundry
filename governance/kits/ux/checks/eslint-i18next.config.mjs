// FG-UX-003: no literal strings in components (eslint-plugin-i18next, flat config, ESM). Pin per the stack kit version-matrix template.
import i18next from 'eslint-plugin-i18next';

export default [
  i18next.configs['flat/recommended'],
  { files: ['src/**/*.{jsx,tsx}'], rules: { 'i18next/no-literal-string': 'error' } },
];
