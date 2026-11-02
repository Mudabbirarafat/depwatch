import type { GatesReport } from '../api'

/**
 * The data-quality gates, which are otherwise invisible.
 *
 * Worth showing on the page: "is this data fresh and complete" is the first
 * question to ask of any dashboard, and most dashboards make you assume it.
 */
export function GatePanel({ report }: { report: GatesReport }) {
  return (
    <section className="rounded-lg border border-zinc-200 p-4 dark:border-zinc-700">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-sm font-semibold">Data quality</h2>
        <span
          className={
            report.passed
              ? 'text-xs font-medium text-band-low dark:text-emerald-400'
              : 'text-xs font-medium text-band-high dark:text-rose-400'
          }
        >
          {report.passed ? 'all gates passed' : 'gates failed'}
        </span>
      </header>
      <ul className="space-y-1.5">
        {report.gates.map((gate) => (
          <li key={gate.name} className="flex items-start gap-2 text-xs">
            <span
              aria-hidden
              className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${
                gate.passed ? 'bg-band-low dark:bg-emerald-400' : 'bg-band-high dark:bg-rose-400'
              }`}
            />
            <span className="font-mono text-zinc-700 dark:text-zinc-300">{gate.name}</span>
            <span className="text-zinc-500 dark:text-zinc-400">{gate.detail}</span>
            <span className="sr-only">{gate.passed ? 'passed' : 'failed'}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
