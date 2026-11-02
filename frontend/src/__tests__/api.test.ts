import { describe, expect, it } from 'vitest'

import { buildAnalyseBody } from '../api'

/**
 * Turning pasted text into a request is the one piece of real logic on this
 * side, and it is the piece that decides whether ranges get resolved at all:
 * send a manifest and the API scores the version you would install, send names
 * and it scores the latest release. Getting that wrong silently understates
 * risk, which is the whole thing the project exists to avoid.
 */
describe('buildAnalyseBody', () => {
  it('sends a package.json as a manifest so ranges are resolved', () => {
    const body = buildAnalyseBody('{"dependencies": {"lodash": "4.17.11"}}')
    expect(body).toEqual({ manifest: { dependencies: { lodash: '4.17.11' } } })
  })

  it('recognises devDependencies and peerDependencies too', () => {
    expect(buildAnalyseBody('{"devDependencies": {"vitest": "^2"}}')).toHaveProperty('manifest')
    expect(buildAnalyseBody('{"peerDependencies": {"react": "^18"}}')).toHaveProperty('manifest')
  })

  it('keeps the whole manifest, not just the dependency section', () => {
    // The API reads every dependency section, so trimming here would drop
    // devDependencies from a manifest that has both.
    const body = buildAnalyseBody(
      '{"name": "x", "dependencies": {"a": "1"}, "devDependencies": {"b": "2"}}',
    )
    expect(body).toEqual({
      manifest: { name: 'x', dependencies: { a: '1' }, devDependencies: { b: '2' } },
    })
  })

  it('treats a JSON object with no dependency section as a list of names', () => {
    expect(buildAnalyseBody('{"lodash": "4.17.11", "express": "4.17.1"}')).toEqual({
      names: ['lodash', 'express'],
    })
  })

  it('treats plain text as names, however it is separated', () => {
    expect(buildAnalyseBody('lodash express')).toEqual({ names: ['lodash', 'express'] })
    expect(buildAnalyseBody('lodash, express')).toEqual({ names: ['lodash', 'express'] })
    expect(buildAnalyseBody('lodash\nexpress\n')).toEqual({ names: ['lodash', 'express'] })
  })

  it('handles a single name, which is valid JSON for neither object nor array', () => {
    expect(buildAnalyseBody('lodash')).toEqual({ names: ['lodash'] })
  })

  it('does not mistake a JSON array or bare value for a manifest', () => {
    // JSON.parse succeeds on these, so the object check has to come after it.
    expect(buildAnalyseBody('["lodash"]')).toEqual({ names: ['["lodash"]'] })
    expect(buildAnalyseBody('42')).toEqual({ names: ['42'] })
    expect(buildAnalyseBody('null')).toEqual({ names: ['null'] })
  })

  it('ignores surrounding whitespace', () => {
    expect(buildAnalyseBody('   lodash   ')).toEqual({ names: ['lodash'] })
  })

  it('produces no empty names from repeated separators', () => {
    expect(buildAnalyseBody('lodash,,  express')).toEqual({ names: ['lodash', 'express'] })
  })
})
