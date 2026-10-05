import { afterEach, describe, expect, it, vi } from 'vitest'
import { api, compareReviewPriority, queryFor, rent, shortDate } from './api'
import type { Listing } from './api'

afterEach(() => vi.unstubAllGlobals())

describe('listing API client', () => {
  it('preserves explicit filter overrides in a shareable query', () => {
    const query = queryFor({ days: 30, max_rent: '0', min_bedrooms: '2', areas: 'Hackney, Islington', max_commute: '' }, { status: 'left' })
    const params = new URLSearchParams(query)
    expect(params.get('status')).toBe('left')
    expect(params.get('max_rent')).toBe('0')
    expect(params.get('areas')).toBe('Hackney, Islington')
    expect(params.has('max_commute')).toBe(false)
  })

  it('writes review decisions and reports failed writes', async () => {
    const fetchMock = vi.fn()
      .mockResolvedValueOnce({ ok: true, json: async () => ({ id: 7, review_status: 'favourite' }) })
      .mockResolvedValueOnce({ ok: false, status: 500 })
    vi.stubGlobal('fetch', fetchMock)
    await expect(api.review(7, 'favourite')).resolves.toMatchObject({ review_status: 'favourite' })
    expect(fetchMock.mock.calls[0][0]).toBe('/api/v1/listings/7/review')
    expect(fetchMock.mock.calls[0][1]).toMatchObject({ method: 'PATCH', body: '{"status":"favourite"}' })
    await expect(api.review(7, 'left')).rejects.toThrow('Request failed (500)')
  })

  it('formats unknown rent without inventing a value', () => {
    expect(rent(null)).toBe('Rent unknown')
    expect(rent(2200)).toBe('£2,200 / mo')
    expect(shortDate('2026-09-30T10:00:00Z')).toContain('Sep')
  })

  it('ranks the slower transit commute before fit score', () => {
    const base = { commute_minutes: 40, assessment: { fit_score: 99 } } as Listing
    const fast = { commute_minutes: 20, assessment: { fit_score: 70 } } as Listing
    const unknown = { commute_minutes: null, assessment: { fit_score: 100 } } as Listing
    expect([base, unknown, fast].sort(compareReviewPriority)).toEqual([fast, base, unknown])
  })
})
