// Smoke test written by the test agent's stub engine (no LLM involved).
import { expect, it } from 'vitest'
import * as module from './slugify'

it('exports its public API', () => {
  expect(Object.keys(module).length).toBeGreaterThan(0)
})
