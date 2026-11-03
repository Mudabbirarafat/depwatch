import { ECOSYSTEMS, type Ecosystem } from '../api'

interface Props {
  value: Ecosystem
  onChange: (ecosystem: Ecosystem) => void
  disabled?: boolean
}

/**
 * Which registry to search.
 *
 * Tabs rather than a select, because there are two of them and a dropdown
 * hides half the answer to "what does this thing cover".
 */
export function EcosystemTabs({ value, onChange, disabled = false }: Props) {
  return (
    <div role="tablist" aria-label="Ecosystem" className="flex gap-1">
      {ECOSYSTEMS.map((ecosystem) => {
        const selected = ecosystem.id === value
        return (
          <button
            key={ecosystem.id}
            role="tab"
            type="button"
            aria-selected={selected}
            disabled={disabled}
            onClick={() => onChange(ecosystem.id)}
            className={
              selected
                ? 'rounded-md bg-blue-600 px-3 py-1.5 text-sm font-semibold text-white'
                : 'rounded-md px-3 py-1.5 text-sm font-semibold text-zinc-500 hover:bg-zinc-100 disabled:opacity-50 dark:text-zinc-400 dark:hover:bg-zinc-800'
            }
          >
            {ecosystem.label}
          </button>
        )
      })}
    </div>
  )
}
