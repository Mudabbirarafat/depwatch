import type { PackageReport } from '../api'

const COMPACT = new Intl.NumberFormat('en', { notation: 'compact' })

/**
 * The GitHub signals, under the package name.
 *
 * Absent for about 18% of packages — hosted elsewhere, or declaring no
 * repository at all — and absent is shown as nothing rather than as zero,
 * which would read as "checked, and nobody is working on it".
 */
export function RepoSignals({ report }: { report: PackageReport }) {
  const { repo_full_name, repo_archived, repo_stars, repo_contributors } = report
  if (!repo_full_name) return null

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
      {repo_archived ? (
        <span className="rounded bg-band-high/10 px-1.5 py-0.5 font-medium text-band-high dark:bg-rose-400/10 dark:text-rose-400">
          archived
        </span>
      ) : null}
      <a
        href={`https://github.com/${repo_full_name}`}
        className="font-mono hover:underline"
        rel="noreferrer"
      >
        {repo_full_name}
      </a>
      {typeof repo_stars === 'number' ? <span>{COMPACT.format(repo_stars)} stars</span> : null}
      {typeof repo_contributors === 'number' ? (
        <span>
          {COMPACT.format(repo_contributors)}{' '}
          {repo_contributors === 1 ? 'contributor' : 'contributors'}
        </span>
      ) : null}
    </div>
  )
}
