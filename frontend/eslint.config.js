import js from '@eslint/js'
import reactHooks from 'eslint-plugin-react-hooks'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['node_modules', 'dist', '../src/depwatch/web'] },
  js.configs.recommended,
  tseslint.configs.recommended,
  // The hooks rules are the ones worth having here: a missing dependency in a
  // useEffect is the classic React bug that type-checking cannot see.
  //
  // `configs.flat[...]` rather than `configs[...]`: the plugin still ships the
  // old eslintrc shape under the top-level key, where `plugins` is an array,
  // and flat config needs it to be an object.
  reactHooks.configs.flat['recommended-latest'],
  {
    files: ['src/**/*.{ts,tsx}'],
    languageOptions: {
      globals: {
        document: 'readonly',
        window: 'readonly',
        fetch: 'readonly',
        console: 'readonly',
        RequestInit: 'readonly',
        HTMLElement: 'readonly',
      },
    },
  },
)
