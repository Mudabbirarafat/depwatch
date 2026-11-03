/**
 * The API, typed.
 *
 * These interfaces mirror the Pydantic models in `src/depwatch/api.py`. They
 * are hand-written rather than generated: the project is small enough that a
 * codegen step would cost more than it saves, and the tests assert on these
 * shapes, so a backend change that breaks them fails the frontend build too.
 */

export type Band = 'low' | 'medium' | 'high' | 'unscored'

/** The ecosystems the warehouse ingests. Mirrors SOURCES in sources/__init__.py. */
export type Ecosystem = 'npm' | 'pypi'

export const ECOSYSTEMS: { id: Ecosystem; label: string; example: string }[] = [
  { id: 'npm', label: 'npm', example: 'package.json' },
  { id: 'pypi', label: 'PyPI', example: 'requirements' },
]

export type Resolution = 'latest' | 'resolved' | 'unsupported-range' | 'no-matching-version'

/** One package as /analyse reports it. */
export interface PackageReport {
  name: string
  ecosystem: string
  latest_version: string | null
  license: string | null
  maintainers: number | null
  deprecated: boolean
  last_published_at: string | null
  requested_range: string | null
  scored_version: string | null
  resolution: Resolution
  advisories: number
  /** null when the warehouse has not scored this package yet. */
  score: number | null
  band: Band
  reasons: string[]
}

export interface AnalyseSummary {
  requested: number
  analysed: number
  unknown: number
  high: number
  medium: number
  low: number
  unresolved_ranges: number
}

export interface AnalyseResponse {
  analysed: PackageReport[]
  unknown: string[]
  summary: AnalyseSummary
}

export interface LastRun {
  id: number
  source: string
  status: string
  finished_at: string | null
}

export interface Health {
  status: string
  packages: number
  advisories: number
  last_run: LastRun | null
}

export interface Gate {
  name: string
  passed: boolean
  detail: string
}

export interface GatesReport {
  passed: boolean
  gates: Gate[]
  checked_at: string
  last_finished_run: Record<string, unknown> | null
}

/** /packages returns scored rows without the manifest-resolution fields. */
export interface StoredPackage {
  name: string
  ecosystem: string
  latest_version: string | null
  license: string | null
  maintainers: number | null
  deprecated: boolean
  last_published_at: string | null
  advisories: number
  score: number | null
  band: Band
  reasons: string[]
  scored_at: string | null
}

export interface PackagesResponse {
  count: number
  total: number
  packages: StoredPackage[]
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init)
  if (!response.ok) {
    // 503 from /gates is a report, not a transport failure: the body is the
    // thing the caller wants. Everything else is an error worth surfacing.
    if (path.startsWith('/gates') && response.status === 503) {
      return (await response.json()) as T
    }
    const detail = await response.text().catch(() => '')
    throw new ApiError(detail.slice(0, 300) || response.statusText, response.status)
  }
  return (await response.json()) as T
}

export const getHealth = () => request<Health>('/healthz')

export const getGates = () => request<GatesReport>('/gates')

export const getPackages = (limit = 200, ecosystem: Ecosystem = 'npm') =>
  request<PackagesResponse>(`/packages?limit=${limit}&ecosystem=${ecosystem}`)

/**
 * Work out what to ask the API for, from whatever was pasted.
 *
 * A package.json goes through as a manifest so ranges resolve to the version
 * actually installed; anything else is treated as a list of names, which are
 * scored against the latest release.
 *
 * Exported separately from the request so it can be tested as what it is: a
 * pure function from text to a request body.
 */
export function buildAnalyseBody(
  input: string,
  ecosystem: Ecosystem = 'npm',
): Record<string, unknown> {
  const text = input.trim()

  try {
    const parsed: unknown = JSON.parse(text)
    if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
      // Valid JSON, but not an object - a bare string or number is a name.
      return { ecosystem, names: text.split(/[\s,]+/).filter(Boolean) }
    }
    const manifest = parsed as Record<string, unknown>
    const hasDependencySection =
      'dependencies' in manifest ||
      'devDependencies' in manifest ||
      'peerDependencies' in manifest
    return hasDependencySection
      ? { ecosystem, manifest }
      : { ecosystem, names: Object.keys(manifest) }
  } catch {
    return { ecosystem, names: text.split(/[\s,]+/).filter(Boolean) }
  }
}

export function analyse(input: string, ecosystem: Ecosystem = 'npm'): Promise<AnalyseResponse> {
  return request<AnalyseResponse>('/analyse', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(buildAnalyseBody(input, ecosystem)),
  })
}

/** A manifest per ecosystem, each pinned to versions that still carry advisories. */
export const RISKY_EXAMPLES: Record<Ecosystem, unknown> = {
  npm: {
    dependencies: {
      lodash: '4.17.11',
      minimist: '1.2.0',
      express: '4.17.1',
      request: '^2.88.0',
      'left-pad': '^1.3.0',
    },
  },
  pypi: {
    dependencies: {
      django: '3.2.0',
      pyyaml: '5.3',
      urllib3: '1.26.4',
      pillow: '9.0.0',
      requests: '2.19.0',
    },
  },
}
