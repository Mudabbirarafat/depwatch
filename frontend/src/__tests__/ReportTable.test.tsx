import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PackageReport } from '../api'
import { ReportTable } from '../components/ReportTable'

function report(overrides: Partial<PackageReport> = {}): PackageReport {
  return {
    name: 'lodash',
    ecosystem: 'npm',
    latest_version: '4.18.1',
    license: 'MIT',
    maintainers: 1,
    deprecated: false,
    last_published_at: '2026-01-01T00:00:00Z',
    requested_range: '4.17.11',
    scored_version: '4.17.11',
    resolution: 'resolved',
    advisories: 10,
    score: 85,
    band: 'high',
    reasons: ['critical severity advisory GHSA-jf85-cpcp-j695', 'single maintainer'],
    ...overrides,
  }
}

describe('ReportTable', () => {
  it('shows every reason behind a score', () => {
    // A number without its reasons is not useful to anyone deciding whether to
    // adopt a package, so the reasons are not an optional detail view.
    render(<ReportTable rows={[report()]} />)

    expect(screen.getByText('critical severity advisory GHSA-jf85-cpcp-j695')).toBeInTheDocument()
    expect(screen.getByText('single maintainer')).toBeInTheDocument()
  })

  it('shows the score with its band, never alone', () => {
    render(<ReportTable rows={[report()]} />)
    expect(screen.getByText('85')).toBeInTheDocument()
    expect(screen.getByText('high')).toBeInTheDocument()
  })

  it('shows which version was scored, not just which was asked for', () => {
    render(<ReportTable rows={[report({ requested_range: '^4.17.1', scored_version: '4.20.0' })]} />)

    expect(screen.getByText('^4.17.1')).toBeInTheDocument()
    expect(screen.getByText('→ 4.20.0')).toBeInTheDocument()
  })

  it('flags a range it could not resolve', () => {
    // The fallback to latest is stated in the report rather than hidden,
    // because scoring the wrong version silently understates risk.
    render(<ReportTable rows={[report({ resolution: 'unsupported-range' })]} />)
    expect(screen.getByText('unsupported-range')).toBeInTheDocument()
  })

  it('does not label a resolved range as a fallback', () => {
    render(<ReportTable rows={[report({ resolution: 'resolved' })]} />)
    expect(screen.queryByText('unsupported-range')).not.toBeInTheDocument()
    expect(screen.queryByText('no-matching-version')).not.toBeInTheDocument()
  })

  it('renders a row per package, keyed so two ecosystems can share a name', () => {
    render(
      <ReportTable
        rows={[
          report({ name: 'express', ecosystem: 'npm' }),
          report({ name: 'express', ecosystem: 'pypi' }),
        ]}
      />,
    )
    expect(screen.getAllByText('express')).toHaveLength(2)
  })

  it('copes with a package that has no reasons', () => {
    render(<ReportTable rows={[report({ reasons: [], score: 0, band: 'low' })]} />)
    const row = screen.getByText('lodash').closest('tr')
    expect(row).not.toBeNull()
    expect(within(row as HTMLElement).getByText('0')).toBeInTheDocument()
  })

  it('shows a dash where no range was requested', () => {
    render(<ReportTable rows={[report({ requested_range: null, resolution: 'latest' })]} />)
    expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  })
})
