import type { PackageReport } from '../api'
import { ScoreBadge } from './ScoreBadge'

/**
 * The report. Every row carries its reasons, because a number without an
 * explanation is not useful to anyone deciding whether to adopt a package.
 */
export function ReportTable({ rows }: { rows: PackageReport[] }) {
  return (
    <table className="w-full border-collapse text-sm">
      <thead>
        <tr className="border-b border-zinc-200 dark:border-zinc-700">
          <Th className="pr-3">Package</Th>
          <Th className="hidden pr-3 sm:table-cell">Range / scored</Th>
          {/* pr-3 matters at phone width: without it these two headers run
              together into one unreadable word. */}
          <Th className="pr-3 text-right">Advisories</Th>
          <Th className="text-right">Score</Th>
        </tr>
      </thead>
      <tbody>
        {rows.map((row) => (
          <tr
            key={`${row.ecosystem}:${row.name}`}
            className="border-b border-zinc-100 align-top dark:border-zinc-800"
          >
            <td className="py-3 pr-3">
              <div className="font-semibold">{row.name}</div>
              <ul className="mt-1 list-disc space-y-0.5 pl-4 text-xs text-zinc-500 dark:text-zinc-400">
                {row.reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
              {/* On a narrow screen the range column is hidden, so the
                  resolved version moves under the name rather than vanishing. */}
              <div className="mt-1 font-mono text-xs text-zinc-500 sm:hidden dark:text-zinc-400">
                {row.requested_range ?? '—'} → {row.scored_version ?? '?'}
              </div>
            </td>
            <td className="hidden py-3 pr-3 font-mono text-xs text-zinc-500 sm:table-cell dark:text-zinc-400">
              <div>{row.requested_range ?? '—'}</div>
              <div className="mt-1">→ {row.scored_version ?? '?'}</div>
              {row.resolution !== 'resolved' && row.resolution !== 'latest' ? (
                <div className="mt-1 text-amber-600 dark:text-amber-400">{row.resolution}</div>
              ) : null}
            </td>
            <td className="py-3 pr-3 text-right tabular-nums">{row.advisories}</td>
            <td className="py-3">
              <ScoreBadge score={row.score} band={row.band} />
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Th({ children, className = '' }: { children: React.ReactNode; className?: string }) {
  return (
    <th
      className={`pb-2 text-left text-xs font-medium uppercase tracking-wider text-zinc-500 dark:text-zinc-400 ${className}`}
    >
      {children}
    </th>
  )
}
