interface Props {
  value: string
  onChange: (value: string) => void
  onAnalyse: () => void
  onLoadExample: () => void
  onShowAll: () => void
  busy: boolean
}

/** Paste a package.json, or a list of names, and score it. */
export function ManifestForm({
  value,
  onChange,
  onAnalyse,
  onLoadExample,
  onShowAll,
  busy,
}: Props) {
  return (
    <div>
      <label htmlFor="manifest" className="sr-only">
        package.json or dependency names
      </label>
      <textarea
        id="manifest"
        value={value}
        spellCheck={false}
        onChange={(event) => onChange(event.target.value)}
        placeholder={'{\n  "dependencies": {\n    "lodash": "^4.17.21"\n  }\n}'}
        className="h-40 w-full resize-y rounded-lg border border-zinc-200 bg-zinc-50 p-3 font-mono text-xs text-zinc-900 placeholder:text-zinc-400 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-100"
      />
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          type="button"
          onClick={onAnalyse}
          disabled={busy || !value.trim()}
          className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-default disabled:opacity-50"
        >
          {busy ? 'Analysing…' : 'Analyse'}
        </button>
        {/* Fills the box *and* runs it: one click from landing on the page to
            seeing a real report is the difference between a demo someone tries
            and a demo someone reads about. */}
        <button
          type="button"
          onClick={onLoadExample}
          disabled={busy}
          className="rounded-md border border-zinc-200 px-4 py-2 text-sm font-semibold text-blue-600 hover:bg-zinc-50 disabled:opacity-50 dark:border-zinc-700 dark:text-blue-400 dark:hover:bg-zinc-800"
        >
          Load a risky example
        </button>
        <button
          type="button"
          onClick={onShowAll}
          className="rounded-md border border-zinc-200 px-4 py-2 text-sm font-semibold text-blue-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-blue-400 dark:hover:bg-zinc-800"
        >
          Show everything ingested
        </button>
      </div>
    </div>
  )
}
