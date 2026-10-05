import { afterEach, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import App, { Gallery, Photo } from './App'
import type { Listing } from './api'

vi.mock('./MapView', () => ({ default: () => <div data-testid="map-view" /> }))

afterEach(() => { cleanup(); sessionStorage.clear(); vi.unstubAllGlobals() })

const listing: Listing = {
  id: 7, source: 'rental-alert-email', url: 'https://example.test/7',
  title: 'One bedroom flat in Islington', address: 'Islington N1', postcode: 'N1',
  description: null, monthly_rent: 2000, bedrooms: 1, bathrooms: null,
  available_from: null, availability_note: null, first_seen_at: '2026-10-01T10:00:00Z', email_received_at: null,
  latitude: 51.53, longitude: -0.1, commute_minutes: 35,
  review_status: 'unreviewed', reviewed_at: null, image_urls: [], routes: [],
  commute_provider: null, page_reviewed_at: null, ai_review: null,
  assessment: {
    matches_filters: true, fit_score: 89, score_reasons: ['Rent is under budget'],
    evidence_sources: { Rent: 'Alert email', Bathrooms: 'Unknown' },
    missing_fields: ['Bathrooms'], known_fields: 1, exclusion_reasons: [],
  },
}

it('keeps a listing visible after a failed review write, then removes it on success', async () => {
  let writeCount = 0
  let saved = false
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 1, matched: 1, awaiting_review: saved ? 0 : 1, favourites: saved ? 1 : 0 }) }
    if (input.includes('/listings?')) return { ok: true, json: async () => ({ items: saved ? [] : [listing], total: saved ? 0 : 1, offset: 0, next_offset: null }) }
    if (options?.method === 'PATCH') {
      writeCount += 1
      if (writeCount === 1) return { ok: false, status: 500 }
      saved = true
      return { ok: true, json: async () => ({ id: 7, review_status: 'favourite', reviewed_at: '2026-10-01T10:10:00Z' }) }
    }
    throw new Error(`Unexpected request: ${input}`)
  }))

  render(<MemoryRouter><App /></MemoryRouter>)
  await screen.findByText('One bedroom flat in Islington')
  expect((vi.mocked(fetch).mock.calls as [string][]).some(([input]) => input.includes('/listings?') && input.includes('sort=commute'))).toBe(true)
  fireEvent.click(screen.getByRole('button', { name: 'Favourite listing' }))
  await screen.findByText(/Choice not saved/)
  expect(screen.getByText('One bedroom flat in Islington')).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Favourite listing' }))
  await waitFor(() => expect(screen.getByText('All caught up')).toBeTruthy())
  expect(writeCount).toBe(2)
})

it('tries the next photo and falls back when all images fail', () => {
  render(<Photo listing={{ ...listing, image_urls: [
    'https://example.test/broken.jpg', 'https://example.test/also-broken.jpg',
  ] }} />)
  const photo = screen.getByRole('img', { name: `Photo of ${listing.title}` })
  fireEvent.error(photo)
  expect(photo.getAttribute('src')).toBe('https://example.test/also-broken.jpg')
  fireEvent.error(photo)
  expect(screen.getByRole('img', { name: 'No property photo available' })).toBeTruthy()
})

it('skips tiny tracking pixels even when they load successfully', () => {
  render(<Photo listing={{ ...listing, image_urls: [
    'https://example.test/pixel.png', 'https://example.test/home.jpg',
  ] }} />)
  const photo = screen.getByRole('img', { name: `Photo of ${listing.title}` })
  Object.defineProperty(photo, 'naturalWidth', { configurable: true, value: 1 })
  Object.defineProperty(photo, 'naturalHeight', { configurable: true, value: 1 })
  fireEvent.load(photo)
  expect(photo.getAttribute('src')).toBe('https://example.test/home.jpg')
})

it('lets a reader swipe and use buttons to browse valid photos', async () => {
  vi.stubGlobal('Image', class {
    onload: (() => void) | null = null
    onerror: (() => void) | null = null
    naturalWidth = 600
    naturalHeight = 400
    set src(_value: string) { queueMicrotask(() => this.onload?.()) }
  })
  render(<Gallery listing={{ ...listing, image_urls: ['https://example.test/first.jpg', 'https://example.test/second.jpg'] }} />)
  const gallery = await screen.findByRole('region', { name: `Photos of ${listing.title}` })
  expect(screen.getByRole('img', { name: /photo 1 of 2/ })).toBeTruthy()
  fireEvent.touchStart(gallery, { touches: [{ clientX: 200, clientY: 100 }] })
  fireEvent.touchEnd(gallery, { changedTouches: [{ clientX: 100, clientY: 105 }] })
  expect(screen.getByRole('img', { name: /photo 2 of 2/ })).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Previous photo' }))
  expect(screen.getByRole('img', { name: /photo 1 of 2/ })).toBeTruthy()
})

it('offers photo controls on the review card without saving a decision', async () => {
  vi.stubGlobal('Image', class {
    onload: (() => void) | null = null
    naturalWidth = 600
    naturalHeight = 400
    set src(_value: string) { queueMicrotask(() => this.onload?.()) }
  })
  const writes: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string, options?: RequestInit) => {
    if (options?.method === 'PATCH') writes.push(input)
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 1, matched: 1, awaiting_review: 1, favourites: 0 }) }
    if (input.includes('/listings?')) return { ok: true, json: async () => ({ items: [{ ...listing, image_urls: ['https://example.test/one.jpg', 'https://example.test/two.jpg'] }], total: 1, offset: 0, next_offset: null }) }
    throw new Error(`Unexpected request: ${input}`)
  }))
  render(<MemoryRouter><App /></MemoryRouter>)
  await screen.findByRole('img', { name: /photo 1 of 2/ })
  fireEvent.click(screen.getByRole('button', { name: 'Next photo' }))
  expect(screen.getByRole('img', { name: /photo 2 of 2/ })).toBeTruthy()
  expect(writes).toEqual([])
})

it('shares Browse decision and commute filters in the URL', async () => {
  const requests: string[] = []
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(input)
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 1, matched: 1, awaiting_review: 1, favourites: 0 }) }
    if (input.includes('/listings?')) return { ok: true, json: async () => ({ items: [listing], total: 1, offset: 0, next_offset: null }) }
    throw new Error(`Unexpected request: ${input}`)
  }))
  render(<MemoryRouter initialEntries={['/browse']}><App /></MemoryRouter>)
  await screen.findByText('One bedroom flat in Islington')
  fireEvent.change(screen.getByLabelText('Decision'), { target: { value: 'reviewed' } })
  fireEvent.change(screen.getByLabelText('Max transit commute'), { target: { value: '40' } })
  await waitFor(() => expect(requests.some(request => request.includes('/listings?') && request.includes('status=reviewed') && request.includes('commute_at_most=40'))).toBe(true))
  fireEvent.click(screen.getByLabelText('Match saved search'))
  await waitFor(() => expect(requests.some(request => request.includes('/listings?') && request.includes('matching_only=true'))).toBe(true))
})

it('returns from detail to the same filtered Browse position and loaded page', async () => {
  const requests: string[] = []
  const second = { ...listing, id: 8, title: 'Second flat in Hackney' }
  const scrollTo = vi.fn()
  vi.stubGlobal('scrollTo', scrollTo)
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callback(0); return 1 })
  vi.stubGlobal('scrollY', 840)
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(input)
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 2, matched: 2, awaiting_review: 2, favourites: 0 }) }
    if (input.includes('/listings/8?')) return { ok: true, json: async () => second }
    if (input.includes('/listings?')) {
      const offset = new URL(input, 'http://localhost').searchParams.get('offset')
      return { ok: true, json: async () => offset === '1'
        ? { items: [second], total: 2, offset: 1, next_offset: null }
        : { items: [listing], total: 2, offset: 0, next_offset: 1 } }
    }
    throw new Error(`Unexpected request: ${input}`)
  }))
  render(<MemoryRouter initialEntries={['/browse?days=30&status=reviewed&commute_at_most=35']}><App /></MemoryRouter>)
  await screen.findByText('One bedroom flat in Islington')
  fireEvent.click(screen.getByRole('button', { name: 'Load more' }))
  await screen.findByText('Second flat in Hackney')
  fireEvent.click(screen.getAllByRole('link', { name: 'Full details ↗' })[1])
  await screen.findByRole('heading', { name: 'Second flat in Hackney' })
  fireEvent.click(screen.getByRole('link', { name: '← Back to homes' }))
  await screen.findByText('Second flat in Hackney')
  await waitFor(() => expect(scrollTo).toHaveBeenCalledWith(0, 840))
  expect(requests.some(request => request.includes('/listings?') && request.includes('status=reviewed') && request.includes('commute_at_most=35') && request.includes('offset=1'))).toBe(true)
})

it('maps all recent homes by default and shows commute comparisons for a selected home', async () => {
  const requests: string[] = []
  const routed = { ...listing, commute_provider: 'google-routes' as const, routes: [
    { destination: 'Office', mode: 'transit', minutes: 36, url: 'https://www.google.com/maps/dir/' },
    { destination: 'Office', mode: 'walking', minutes: 62, url: 'https://www.google.com/maps/dir/?walking' },
  ] }
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    requests.push(input)
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 1, matched: 1, awaiting_review: 1, favourites: 0 }) }
    if (input.includes('/listings?')) return { ok: true, json: async () => ({ items: [routed], total: 1, offset: 0, next_offset: null }) }
    throw new Error(`Unexpected request: ${input}`)
  }))

  render(<MemoryRouter initialEntries={['/map']}><App /></MemoryRouter>)
  await screen.findByText('One bedroom flat in Islington')
  expect(requests.find(request => request.includes('/listings?'))).not.toContain('matching_only=true')
  fireEvent.click(screen.getByRole('button', { name: /One bedroom flat in Islington/ }))
  expect(screen.getByRole('heading', { name: 'Calculated commutes' })).toBeTruthy()
  expect(screen.getByText('Google Maps')).toBeTruthy()
  expect(screen.getByText('36 min')).toBeTruthy()
  expect(screen.getAllByRole('link', { name: 'View route ↗' })).toHaveLength(2)
  expect(screen.getByText(/Walking routes may not include clear sidewalks/)).toBeTruthy()
  fireEvent.click(screen.getByRole('button', { name: 'Matches only' }))
  await waitFor(() => expect(requests.some(request => request.includes('/listings?') && request.includes('matching_only=true'))).toBe(true))
})

it('shows an AI suggestion without changing the shared review status', async () => {
  const reviewed = { ...listing, ai_review: {
    summary: 'Short transit journey; bathroom details are missing.', recommendation: 'maybe' as const,
    strengths: ['Commute'], concerns: [], unknowns: ['Bathrooms'], model: 'test-model',
    reviewed_at: '2026-10-04T07:00:00Z',
  } }
  vi.stubGlobal('fetch', vi.fn(async (input: string) => {
    if (input.includes('/summary')) return { ok: true, json: async () => ({ imported: 1, matched: 1, awaiting_review: 1, favourites: 0 }) }
    if (input.includes('/listings/7?')) return { ok: true, json: async () => reviewed }
    throw new Error(`Unexpected request: ${input}`)
  }))

  render(<MemoryRouter initialEntries={['/listing/7']}><App /></MemoryRouter>)
  expect(await screen.findByRole('heading', { name: 'maybe' })).toBeTruthy()
  expect(screen.getByText(/never changes automatically/)).toBeTruthy()
  expect(screen.getByText('To review')).toBeTruthy()
})
