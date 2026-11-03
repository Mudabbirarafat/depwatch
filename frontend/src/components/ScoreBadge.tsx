import type { Band } from '../api'

const BAND_STYLES: Record<Band, string> = {
  high: 'text-band-high dark:text-rose-400',
  medium: 'text-band-medium dark:text-amber-400',
  low: 'text-band-low dark:text-emerald-400',
  // Deliberately grey rather than green: nothing has been checked.
  unscored: 'text-zinc-400 dark:text-zinc-500',
}

/** A score is never shown without its band: 85 means nothing on its own.
 *
 *  A package the warehouse has not scored shows a dash, not a zero - zero
 *  would read as "we checked and it is clean". */
export function ScoreBadge({ score, band }: { score: number | null; band: Band }) {
  return (
    <div className={`text-right tabular-nums ${BAND_STYLES[band]}`}>
      <div className="text-lg font-bold leading-none">{score ?? '—'}</div>
      <div className="mt-1 text-xs font-medium uppercase tracking-wide">{band}</div>
    </div>
  )
}
