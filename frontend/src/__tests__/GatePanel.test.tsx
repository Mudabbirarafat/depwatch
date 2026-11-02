import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { GatesReport } from '../api'
import { GatePanel } from '../components/GatePanel'

function gates(overrides: Partial<GatesReport> = {}): GatesReport {
  return {
    passed: true,
    gates: [
      { name: 'warehouse_has_data', passed: true, detail: '10 packages' },
      { name: 'advisories_were_fetched', passed: true, detail: '84 advisories' },
      { name: 'data_is_fresh', passed: true, detail: 'last finished run was 0.4h ago, limit 50h' },
    ],
    checked_at: '2026-11-02T12:00:00Z',
    last_finished_run: null,
    ...overrides,
  }
}

describe('GatePanel', () => {
  it('lists every gate with its detail', () => {
    render(<GatePanel report={gates()} />)

    expect(screen.getByText('warehouse_has_data')).toBeInTheDocument()
    expect(screen.getByText('10 packages')).toBeInTheDocument()
    expect(screen.getByText('last finished run was 0.4h ago, limit 50h')).toBeInTheDocument()
  })

  it('says so when everything passed', () => {
    render(<GatePanel report={gates()} />)
    expect(screen.getByText('all gates passed')).toBeInTheDocument()
  })

  it('says so when a gate failed', () => {
    const failing = gates({
      passed: false,
      gates: [
        { name: 'data_is_fresh', passed: false, detail: 'last finished run was 40.0h ago' },
      ],
    })
    render(<GatePanel report={failing} />)

    expect(screen.getByText('gates failed')).toBeInTheDocument()
    expect(screen.getByText('last finished run was 40.0h ago')).toBeInTheDocument()
  })

  it('states pass and fail in text, not only colour', () => {
    // The dot carries the status visually; a screen reader needs the word, and
    // so does anyone who cannot distinguish the two colours.
    const mixed = gates({
      passed: false,
      gates: [
        { name: 'warehouse_has_data', passed: true, detail: '10 packages' },
        { name: 'data_is_fresh', passed: false, detail: 'stale' },
      ],
    })
    render(<GatePanel report={mixed} />)

    expect(screen.getByText('passed')).toBeInTheDocument()
    expect(screen.getByText('failed')).toBeInTheDocument()
  })
})
