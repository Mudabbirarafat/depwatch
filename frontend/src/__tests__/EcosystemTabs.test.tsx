import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { EcosystemTabs } from '../components/EcosystemTabs'

describe('EcosystemTabs', () => {
  it('shows every ecosystem, so the coverage is visible without clicking', () => {
    render(<EcosystemTabs value="npm" onChange={() => {}} />)

    expect(screen.getByRole('tab', { name: 'npm' })).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'PyPI' })).toBeInTheDocument()
  })

  it('marks the selected one for assistive technology, not only in colour', () => {
    render(<EcosystemTabs value="pypi" onChange={() => {}} />)

    expect(screen.getByRole('tab', { name: 'PyPI' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'npm' })).toHaveAttribute('aria-selected', 'false')
  })

  it('reports a change', async () => {
    const onChange = vi.fn()
    render(<EcosystemTabs value="npm" onChange={onChange} />)

    await userEvent.click(screen.getByRole('tab', { name: 'PyPI' }))
    expect(onChange).toHaveBeenCalledWith('pypi')
  })

  it('is inert while a request is in flight', async () => {
    const onChange = vi.fn()
    render(<EcosystemTabs value="npm" onChange={onChange} disabled />)

    await userEvent.click(screen.getByRole('tab', { name: 'PyPI' }))
    expect(onChange).not.toHaveBeenCalled()
  })
})
