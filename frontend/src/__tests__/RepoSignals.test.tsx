import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { PackageReport } from '../api'
import { RepoSignals } from '../components/RepoSignals'

function report(overrides: Partial<PackageReport> = {}): PackageReport {
  return {
    name: 'requests',
    ecosystem: 'pypi',
    latest_version: '2.34.2',
    license: 'Apache-2.0',
    maintainers: 3,
    deprecated: false,
    last_published_at: null,
    requested_range: null,
    scored_version: '2.34.2',
    resolution: 'latest',
    advisories: 16,
    score: 0,
    band: 'low',
    reasons: [],
    repo_full_name: 'psf/requests',
    repo_stars: 54594,
    repo_contributors: 795,
    repo_archived: false,
    ...overrides,
  }
}

describe('RepoSignals', () => {
  it('shows the repository and its counts', () => {
    render(<RepoSignals report={report()} />)

    expect(screen.getByText('psf/requests')).toBeInTheDocument()
    expect(screen.getByText('55K stars')).toBeInTheDocument()
    expect(screen.getByText('795 contributors')).toBeInTheDocument()
  })

  it('renders nothing for a package that has no GitHub repository', () => {
    // About 18% of them — hosted elsewhere, or declaring no repository. Absent
    // must look absent, not like a repository with zero of everything.
    const { container } = render(<RepoSignals report={report({ repo_full_name: null })} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('calls out an archived repository', () => {
    render(<RepoSignals report={report({ repo_archived: true })} />)
    expect(screen.getByText('archived')).toBeInTheDocument()
  })

  it('says nothing about archiving when the repository is live', () => {
    render(<RepoSignals report={report()} />)
    expect(screen.queryByText('archived')).not.toBeInTheDocument()
  })

  it('omits counts it does not have rather than showing zero', () => {
    render(<RepoSignals report={report({ repo_stars: null, repo_contributors: null })} />)

    expect(screen.getByText('psf/requests')).toBeInTheDocument()
    expect(screen.queryByText(/stars/)).not.toBeInTheDocument()
    expect(screen.queryByText(/contributor/)).not.toBeInTheDocument()
  })

  it('gets the singular right', () => {
    render(<RepoSignals report={report({ repo_contributors: 1 })} />)
    expect(screen.getByText('1 contributor')).toBeInTheDocument()
  })

  it('links to the repository', () => {
    render(<RepoSignals report={report()} />)
    expect(screen.getByRole('link', { name: 'psf/requests' })).toHaveAttribute(
      'href',
      'https://github.com/psf/requests',
    )
  })
})
