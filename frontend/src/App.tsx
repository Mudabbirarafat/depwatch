import { useMutation, useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import {
  analyse,
  getGates,
  getHealth,
  getPackages,
  RISKY_EXAMPLE,
  type AnalyseResponse,
  type PackageReport,
  type StoredPackage,
} from './api'
import { GatePanel } from './components/GatePanel'
import { ManifestForm } from './components/ManifestForm'
import { ReportTable } from './components/ReportTable'

/** A stored row has no manifest range; it was scored against its latest release. */
function asReport(stored: StoredPackage): PackageReport {
  return {
    ...stored,
    requested_range: null,
    scored_version: stored.latest_version,
    resolution: 'latest',
  }
}

function healthLine(
  isError: boolean,
  isLoading: boolean,
  data: { packages: number; advisories: number; last_run: { source: string; status: string } | null } | undefined,
): string {
  if (isError) return 'could not reach the API'
  if (isLoading || !data) return 'checking…'
  if (data.packages === 0) return 'first boot — ingesting seed packages, this takes about a minute…'

  const counts = `${data.packages} packages · ${data.advisories} advisories`
  if (!data.last_run) return counts
  return `${counts} · last run ${data.last_run.source} (${data.last_run.status})`
}

export default function App() {
  const [input, setInput] = useState('')
  const [result, setResult] = useState<AnalyseResponse | null>(null)
  const [truncated, setTruncated] = useState(0)

  const health = useQuery({
    queryKey: ['health'],
    queryFn: getHealth,
    // A cold free instance seeds itself in the background, so keep asking
    // until there is something to show rather than reporting an empty table.
    refetchInterval: (query) => (query.state.data?.packages === 0 ? 5_000 : false),
  })

  const gates = useQuery({ queryKey: ['gates'], queryFn: getGates })

  const analysis = useMutation({
    mutationFn: analyse,
    onSuccess: (data) => {
      setResult(data)
      setTruncated(0)
    },
  })

  const everything = useMutation({
    mutationFn: () => getPackages(),
    onSuccess: (data) => {
      setResult({
        analysed: data.packages.map(asReport),
        unknown: [],
        summary: {
          requested: data.count,
          analysed: data.count,
          unknown: 0,
          high: data.packages.filter((p) => p.band === 'high').length,
          medium: data.packages.filter((p) => p.band === 'medium').length,
          low: data.packages.filter((p) => p.band === 'low').length,
          unresolved_ranges: 0,
        },
      })
      setTruncated(data.total > data.count ? data.total : 0)
    },
  })

  const busy = analysis.isPending || everything.isPending
  const error = analysis.error ?? everything.error

  // The example is passed to the mutation directly rather than read back from
  // state: setInput schedules a render, it does not update `input` here.
  const loadExample = () => {
    const text = JSON.stringify(RISKY_EXAMPLE, null, 2)
    setInput(text)
    analysis.mutate(text)
  }

  return (
    <div className="mx-auto max-w-4xl px-4 py-10 sm:py-14">
      <header className="mb-6">
        <h1 className="text-2xl font-bold tracking-tight">depwatch</h1>
        <p className="mt-1 text-zinc-500 dark:text-zinc-400">
          Which of your dependencies are unmaintained, single-maintainer, or carrying advisories
          your pinned version has not fixed.
        </p>
        <p className="mt-3 text-sm text-zinc-500 dark:text-zinc-400" role="status">
          {healthLine(health.isError, health.isLoading, health.data)}
        </p>
      </header>

      {gates.data ? (
        <div className="mb-6">
          <GatePanel report={gates.data} />
        </div>
      ) : null}

      <ManifestForm
        value={input}
        onChange={setInput}
        onAnalyse={() => analysis.mutate(input)}
        onLoadExample={loadExample}
        onShowAll={() => everything.mutate()}
        busy={busy}
      />

      {error ? (
        <p className="mt-6 text-sm text-band-high dark:text-rose-400">
          {error instanceof Error ? error.message : 'request failed'}
        </p>
      ) : null}

      {result ? (
        <section className="mt-8">
          {result.analysed.length === 0 ? (
            <p className="text-sm text-zinc-500 dark:text-zinc-400">
              Nothing matched.
              {result.unknown.length > 0 ? (
                <>
                  {' '}
                  Not yet ingested:{' '}
                  <code className="font-mono">{result.unknown.join(', ')}</code>
                </>
              ) : null}
            </p>
          ) : (
            <>
              <ReportTable rows={result.analysed} />
              <p className="mt-4 text-sm text-zinc-500 dark:text-zinc-400">
                {result.summary.analysed} analysed · {result.summary.high} high ·{' '}
                {result.summary.medium} medium · {result.summary.low} low
                {result.summary.unresolved_ranges > 0
                  ? ` · ${result.summary.unresolved_ranges} range(s) not resolvable`
                  : ''}
                {truncated > 0
                  ? ` · showing the worst ${result.summary.analysed} of ${truncated}`
                  : ''}
                {result.unknown.length > 0
                  ? ` · not ingested: ${result.unknown.join(', ')}`
                  : ''}
              </p>
            </>
          )}
        </section>
      ) : null}

      <footer className="mt-12 text-xs text-zinc-500 dark:text-zinc-400">
        Scores are explained, never bare. Advisories count only when your resolved version is below
        the published fix.{' '}
        <a className="text-blue-600 hover:underline dark:text-blue-400" href="/docs">
          API docs
        </a>{' '}
        ·{' '}
        <a
          className="text-blue-600 hover:underline dark:text-blue-400"
          href="https://github.com/Mudabbirarafat/depwatch"
        >
          source
        </a>
      </footer>
    </div>
  )
}
