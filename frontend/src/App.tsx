import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { Link, NavLink, Route, Routes, useLocation, useParams, useSearchParams } from 'react-router-dom'
import { api, compareReviewPriority, queryFor, rent, shortDate } from './api'
import type { Filters, Listing, ListingPage, ReviewStatus } from './api'

const MapView = lazy(() => import('./MapView'))

const navigation = [
  { to: '/', label: 'Review', icon: '◈' },
  { to: '/map', label: 'Map', icon: '⌖' },
  { to: '/saved', label: 'Saved', icon: '♡' },
  { to: '/browse', label: 'Browse', icon: '▦' },
]

const statusLabels: Record<ReviewStatus, string> = {
  unreviewed: 'To review', left: 'For later', favourite: 'Favourite', rejected: 'Passed',
}

const listPositionKey = 'goodnest:list-position'

interface ListPosition {
  path: string
  itemCount: number
  scrollY: number
}

function rememberListPosition(path: string, itemCount: number) {
  try {
    sessionStorage.setItem(listPositionKey, JSON.stringify({ path, itemCount, scrollY: window.scrollY }))
  } catch { return }
}

function useListPositionRestoration(page: ListingPage | null, loading: boolean, loadMore: () => void) {
  const location = useLocation()
  const [position] = useState<ListPosition | null>(() => {
    try {
      const value = sessionStorage.getItem(listPositionKey)
      return value ? JSON.parse(value) as ListPosition : null
    } catch { return null }
  })
  const restored = useRef(false)
  useEffect(() => {
    if (restored.current || !position || position.path !== location.pathname + location.search || !page || loading) return
    if (page.items.length < position.itemCount && page.next_offset !== null) {
      loadMore()
      return
    }
    restored.current = true
    window.requestAnimationFrame(() => {
      window.scrollTo(0, position.scrollY)
      try { sessionStorage.removeItem(listPositionKey) } catch { return }
    })
  }, [page, loading, location.pathname, location.search, position, loadMore])
}

function useFilters() {
  const [params, setParams] = useSearchParams()
  const filters: Filters = useMemo(() => ({
    days: Number(params.get('days') || 14),
    max_rent: params.get('max_rent') || '',
    min_bedrooms: params.get('min_bedrooms') || '',
    areas: params.get('areas') || '',
    max_commute: params.get('max_commute') || '',
  }), [params])
  const update = (key: keyof Filters, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  return { filters, update }
}

function useResource<T>(load: () => Promise<T>, dependency: string) {
  const [value, setValue] = useState<T | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    load().then(result => { if (active) setValue(result) })
      .catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Something went wrong') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [dependency])
  return { value, error, loading }
}

function usePagedListings(query: string, revision: number) {
  const [page, setPage] = useState<ListingPage | null>(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  useEffect(() => { setOffset(0); setPage(null) }, [query, revision])
  useEffect(() => {
    let active = true
    setLoading(true)
    setError('')
    api.listings(`${query}&limit=100&offset=${offset}`).then(result => {
      if (!active) return
      setPage(previous => offset === 0 || !previous
        ? result
        : { ...result, items: [...previous.items, ...result.items] })
    }).catch(reason => { if (active) setError(reason instanceof Error ? reason.message : 'Unable to load listings') })
      .finally(() => { if (active) setLoading(false) })
    return () => { active = false }
  }, [query, offset, revision])
  return { page, error, loading, loadMore: () => setOffset(page?.next_offset ?? offset) }
}

function PhotoFallback({ className = '' }: { className?: string }) {
  return <div className={`photo-fallback ${className}`} role="img" aria-label="No property photo available"><span>⌂</span><small>Photo unavailable</small></div>
}

export function Photo({ listing, className = '' }: { listing: Listing; className?: string }) {
  const [index, setIndex] = useState(0)
  const imageKey = listing.image_urls.join('|')
  useEffect(() => setIndex(0), [listing.id, imageKey])
  const imageUrl = listing.image_urls[index]
  if (!imageUrl) return <PhotoFallback className={className} />
  const tryNext = () => setIndex(current => current + 1)
  return <img className={className} src={imageUrl} alt={`Photo of ${listing.title}`}
    onError={tryNext} onLoad={event => {
      if (event.currentTarget.naturalWidth < 120 || event.currentTarget.naturalHeight < 80) tryNext()
    }} />
}

export function Gallery({ listing, swipeEnabled = true }: { listing: Listing; swipeEnabled?: boolean }) {
  const [photos, setPhotos] = useState<string[] | null>(null)
  const [active, setActive] = useState(0)
  const touchStart = useRef<{ x: number; y: number } | null>(null)
  const imageKey = listing.image_urls.join('|')
  useEffect(() => {
    let active = true
    setPhotos(null)
    setActive(0)
    Promise.all(listing.image_urls.slice(0, 12).map(url => new Promise<string | null>(resolve => {
      const image = new Image()
      const timeout = window.setTimeout(() => resolve(null), 10000)
      image.onload = () => {
        window.clearTimeout(timeout)
        resolve(image.naturalWidth >= 120 && image.naturalHeight >= 80 ? url : null)
      }
      image.onerror = () => { window.clearTimeout(timeout); resolve(null) }
      image.src = url
    }))).then(results => { if (active) setPhotos(results.filter((url): url is string => url !== null)) })
    return () => { active = false }
  }, [listing.id, imageKey])
  if (photos === null) return <div className="gallery gallery-loading">Checking photos…</div>
  if (!photos.length) return <div className="gallery empty-gallery"><PhotoFallback className="hero-image" /></div>
  const current = Math.min(active, photos.length - 1)
  const move = (step: number) => setActive(index => (index + step + photos.length) % photos.length)
  return <div className="gallery carousel" tabIndex={0} role="region" aria-roledescription="carousel" aria-label={`Photos of ${listing.title}`}
    onKeyDown={event => { if (event.key === 'ArrowLeft') move(-1); if (event.key === 'ArrowRight') move(1) }}
    onTouchStart={event => { if (swipeEnabled) touchStart.current = { x: event.touches[0].clientX, y: event.touches[0].clientY } }}
    onTouchEnd={event => { const start = touchStart.current; touchStart.current = null; if (!start || photos.length < 2) return
      const deltaX = event.changedTouches[0].clientX - start.x
      const deltaY = event.changedTouches[0].clientY - start.y
      if (Math.abs(deltaX) > 45 && Math.abs(deltaX) > Math.abs(deltaY) * 1.2) move(deltaX < 0 ? 1 : -1)
    }}>
    <img src={photos[current]} alt={`${listing.title}, photo ${current + 1} of ${photos.length}`} onError={() => { setPhotos(previous => previous?.filter(url => url !== photos[current]) || []); setActive(0) }} />
    {photos.length > 1 && <><button className="carousel-button previous" onPointerDownCapture={event => event.stopPropagation()} onClick={() => move(-1)} aria-label="Previous photo">‹</button>
      <button className="carousel-button next" onPointerDownCapture={event => event.stopPropagation()} onClick={() => move(1)} aria-label="Next photo">›</button>
      <span className="carousel-count" aria-live="polite">{current + 1} / {photos.length}</span></>}
  </div>
}

function Pill({ children, tone = 'neutral' }: { children: React.ReactNode; tone?: string }) {
  return <span className={`pill pill-${tone}`}>{children}</span>
}

function FactLine({ listing }: { listing: Listing }) {
  return <div className="fact-line">
    <span>{listing.bedrooms === null ? 'Beds unknown' : `${listing.bedrooms} bed`}</span>
    <span>{listing.bathrooms === null ? 'Bath unknown' : `${listing.bathrooms} bath`}</span>
    <span>{listing.commute_minutes === null ? 'Commute unknown' : `${listing.commute_minutes} min commute`}</span>
  </div>
}

function CommuteBreakdown({ listing }: { listing: Listing }) {
  const destinations = [...new Set(listing.routes.map(route => route.destination))]
  const longest = Math.max(60, ...listing.routes.map(route => route.minutes ?? 0))
  return <section className="commute-breakdown" aria-label="Calculated commute comparison">
    <div className="commute-heading"><div><p className="eyebrow">Journey comparison</p><h2>Calculated commutes</h2></div>
      {listing.commute_provider === 'google-routes' && <span className="google-attribution" translate="no">Google Maps</span>}
    </div>
    {destinations.length ? destinations.map(destination => <div className="commute-destination" key={destination}>
      <h3>{destination}</h3>
      {listing.routes.filter(route => route.destination === destination).map(route => <div className="commute-row" key={`${destination}-${route.mode}`}>
        <span className="commute-mode">{route.mode === 'transit' ? 'Transit' : 'Walking'}</span>
        <div className="commute-track" aria-hidden="true"><span className={route.mode} style={{ width: `${Math.max(3, (route.minutes ?? 0) / longest * 100)}%` }} /></div>
        <strong>{route.minutes === null ? '—' : `${route.minutes} min`}</strong>
        {route.url && <a href={route.url} target="_blank" rel="noopener noreferrer">View route ↗</a>}
      </div>)}
    </div>) : <p className="subtle">No calculated routes are available for this listing yet.</p>}
    {listing.commute_provider === 'google-routes' && listing.routes.some(route => route.mode === 'walking') &&
      <p className="commute-warning">Walking routes may not include clear sidewalks or pedestrian paths. Check conditions before travelling.</p>}
    {listing.commute_provider === 'google-routes' && <p className="commute-note">Times are estimates from Google Maps. Open a route to see its path on Google Maps.</p>}
  </section>
}

function StatusActions({ listing, onReview, busy }: { listing: Listing; onReview: (status: ReviewStatus) => Promise<void>; busy: boolean }) {
  return <div className="actions" aria-label="Review actions">
    <button className="action action-pass" disabled={busy} onClick={() => void onReview('rejected')} aria-label="Pass on listing">×<span>Pass</span></button>
    <button className="action action-later" disabled={busy} onClick={() => void onReview('left')} aria-label="Save for later">⌁<span>Later</span></button>
    <button className="action action-save" disabled={busy} onClick={() => void onReview('favourite')} aria-label="Favourite listing">♡<span>Love it</span></button>
    {listing.review_status !== 'unreviewed' && <button className="action action-reset" disabled={busy} onClick={() => void onReview('unreviewed')}>↶<span>Undo</span></button>}
  </div>
}

function ListingCard({ listing, compact = false, loadedCount = 1 }: { listing: Listing; compact?: boolean; loadedCount?: number }) {
  const location = useLocation()
  const from = location.pathname + location.search
  return <article className={`listing-card ${compact ? 'compact' : ''}`}>
    <div className="listing-photo">{compact ? <Photo listing={listing} /> : <Gallery listing={listing} swipeEnabled={false} />}
      <div className="photo-top"><Pill tone="light">{listing.source.replace('rental-alert-', '')}</Pill><Pill tone="light">{listing.email_received_at ? 'Alert ' : 'Imported '}{shortDate(listing.email_received_at || listing.first_seen_at)}</Pill></div>
    </div>
    <div className="listing-body">
      <div className="listing-heading"><div><p className="eyebrow">{listing.address || listing.postcode || 'London · location unknown'}</p><h2>{listing.title}</h2></div><Pill tone={listing.assessment.matches_filters ? 'good' : 'warn'}>{listing.assessment.fit_score === null ? 'No score' : `${listing.assessment.fit_score}% fit`}</Pill></div>
      <p className="rent">{rent(listing.monthly_rent)}</p>
      <FactLine listing={listing} />
      <div className="card-footer"><span>{listing.assessment.missing_fields.length ? `Unknown: ${listing.assessment.missing_fields.slice(0, 2).join(', ')}${listing.assessment.missing_fields.length > 2 ? '…' : ''}` : 'Key facts present'}</span><Link to={`/listing/${listing.id}${location.search}`} state={{ from }} onClick={() => rememberListPosition(from, loadedCount)}>Full details ↗</Link></div>
    </div>
  </article>
}

function ReviewPage({ filters, revision, onChanged }: { filters: Filters; revision: number; onChanged: () => void }) {
  const query = queryFor(filters, { matching_only: 'true', status: 'unreviewed', sort: 'commute' })
  const { page, loading, error, loadMore } = usePagedListings(query, revision)
  const [dismissed, setDismissed] = useState<number[]>([])
  const [busy, setBusy] = useState(false)
  const [writeError, setWriteError] = useState('')
  useEffect(() => setDismissed([]), [query, revision])
  const queue = useMemo(() => (page?.items || []).filter(item => !dismissed.includes(item.id))
    .sort(compareReviewPriority), [page, dismissed])
  const active = queue[0]
  const onReview = async (status: ReviewStatus) => {
    if (!active || busy) return
    setBusy(true); setWriteError('')
    try { await api.review(active.id, status); setDismissed(previous => [...previous, active.id]); onChanged() }
    catch (reason) { setWriteError(reason instanceof Error ? reason.message : 'Could not save your choice') }
    finally { setBusy(false) }
  }
  return <section className="page review-page">
    <div className="section-intro"><p className="eyebrow">Your next place starts here</p><h1>The good ones, first.</h1><p>New rentals that fit your search. Make the call together.</p></div>
    {error && <div className="notice error">{error}</div>}
    {writeError && <div className="notice error">Choice not saved: {writeError}. Try again.</div>}
    {loading && !page && <div className="loading">Finding your matches…</div>}
    {!loading && !active && <div className="empty"><span>✦</span><h2>All caught up</h2><p>No matching homes are waiting for a decision. Broaden your filters or browse all alerts.</p><Link className="button secondary" to={`/browse?${queryFor(filters)}`}>Browse all homes</Link></div>}
    {active && <><div className="deck-meta"><span><strong>{Math.max(0, page!.total - dismissed.length)}</strong> homes to review</span><span>Swipe left to pass · right to love</span></div>
      <motion.div className="swipe-card" drag={busy ? false : 'x'} dragConstraints={{ left: 0, right: 0 }} dragElastic={0.65}
        onDragEnd={(_, info) => { if (info.offset.x < -110) void onReview('rejected'); if (info.offset.x > 110) void onReview('favourite') }}>
        <ListingCard listing={active} loadedCount={page?.items.length} />
      </motion.div><StatusActions listing={active} busy={busy} onReview={onReview} />
      <p className="subtle centered">Evidence shown is from alerts or listing pages, not independent verification.</p></>}
    {page && page.next_offset !== null && queue.length < 5 && <button className="text-button" disabled={loading} onClick={loadMore}>Load more matches</button>}
  </section>
}

function DetailPage({ filters, onChanged }: { filters: Filters; onChanged: () => void }) {
  const { id } = useParams()
  const location = useLocation()
  const query = queryFor(filters)
  const returnPath = (location.state as { from?: string } | null)?.from
  const backPath = returnPath && /^\/(?:browse|saved|map)?(?:\?|$)/.test(returnPath) ? returnPath : `/browse?${query}`
  const [revision, setRevision] = useState(0)
  const { value: listing, error, loading } = useResource(() => api.listing(Number(id), query), `${id}:${query}:${revision}`)
  const [busy, setBusy] = useState(false)
  const [writeError, setWriteError] = useState('')
  const onReview = async (status: ReviewStatus) => {
    if (!listing) return
    setBusy(true); setWriteError('')
    try { await api.review(listing.id, status); setRevision(value => value + 1); onChanged() }
    catch (reason) { setWriteError(reason instanceof Error ? reason.message : 'Could not save your choice') }
    finally { setBusy(false) }
  }
  if (loading && !listing) return <div className="loading">Loading home…</div>
  if (error && !listing) return <div className="notice error">{error}</div>
  if (!listing) return null
  return <section className="page detail-page">
    <Link className="back-link" to={backPath}>← Back to homes</Link>
    <div className="detail-layout"><div><Gallery listing={listing} />
      <div className="detail-main"><p className="eyebrow">{listing.address || listing.postcode || 'Location unknown'} · {listing.source}</p><h1>{listing.title}</h1><p className="rent">{rent(listing.monthly_rent)}</p><FactLine listing={listing} />
        <p>{listing.description || 'No description captured from the alert.'}</p>
        <div className="detail-facts"><div><span>Available</span><strong>{listing.available_from || listing.availability_note || 'Unknown'}</strong></div><div><span>Alert received</span><strong>{listing.email_received_at ? shortDate(listing.email_received_at) : 'Unknown'}</strong></div><div><span>Imported</span><strong>{shortDate(listing.first_seen_at)}</strong></div><div><span>Portal listed</span><strong>Unknown</strong></div><div><span>Current decision</span><strong>{statusLabels[listing.review_status]}</strong></div></div>
        <CommuteBreakdown listing={listing} />
        {listing.ai_review && <section className="ai-review" aria-label="AI listing suggestion"><p className="eyebrow">AI second opinion · {shortDate(listing.ai_review.reviewed_at)}</p><h2>{listing.ai_review.recommendation.replace('_', ' ')}</h2><p>{listing.ai_review.summary}</p>
          {listing.ai_review.strengths.length > 0 && <p><strong>Strengths:</strong> {listing.ai_review.strengths.join('; ')}</p>}
          {listing.ai_review.concerns.length > 0 && <p><strong>Concerns:</strong> {listing.ai_review.concerns.join('; ')}</p>}
          {listing.ai_review.unknowns.length > 0 && <p><strong>Unknown:</strong> {listing.ai_review.unknowns.join('; ')}</p>}
          <small>Suggestion only; your shared review decision never changes automatically.</small></section>}
        {listing.url && <a className="button secondary" href={listing.url} target="_blank" rel="noopener noreferrer">Open original listing ↗</a>}
      </div></div>
      <aside className="evidence-panel"><div className="fit-big">{listing.assessment.fit_score === null ? '—' : `${listing.assessment.fit_score}%`}<small>fit score</small></div><p className="subtle">Based on your filters, not independent verification.</p>
        {listing.assessment.exclusion_reasons.length > 0 && <div className="notice warn">Outside current filters: {listing.assessment.exclusion_reasons.join('; ')}</div>}
        <h2>Why it fits</h2>{listing.assessment.score_reasons.length ? <ul className="reason-list">{listing.assessment.score_reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : <p>Set filters to calculate a score.</p>}
        <h2>Evidence</h2><div className="evidence-list">{Object.entries(listing.assessment.evidence_sources).map(([field, source]) => <div key={field}><span>{field}</span><strong className={source === 'Unknown' ? 'unknown' : ''}>{source}</strong></div>)}</div>
        {listing.page_reviewed_at && <p className="subtle">Listing page reviewed {shortDate(listing.page_reviewed_at)}</p>}
        {writeError && <div className="notice error">{writeError}</div>}
        <StatusActions listing={listing} busy={busy} onReview={onReview} />
      </aside></div>
  </section>
}

function ListingGrid({ listings }: { listings: Listing[] }) {
  return <div className="listing-grid">{listings.map(listing => <ListingCard key={listing.id} listing={listing} loadedCount={listings.length} compact />)}</div>
}

function SavedPage({ filters, revision }: { filters: Filters; revision: number }) {
  const [params, setParams] = useSearchParams()
  const tab = params.get('saved') === 'left' ? 'left' : 'favourite'
  const setTab = (nextTab: 'favourite' | 'left') => {
    const next = new URLSearchParams(params)
    if (nextTab === 'left') next.set('saved', 'left')
    else next.delete('saved')
    setParams(next)
  }
  const query = queryFor(filters, { status: tab })
  const { page, loading, error, loadMore } = usePagedListings(query, revision)
  useListPositionRestoration(page, loading, loadMore)
  return <section className="page"><div className="section-intro"><p className="eyebrow">The shortlist</p><h1>Worth another look.</h1><p>Your shared decisions, all in one place.</p></div>
    <div className="segmented"><button className={tab === 'favourite' ? 'active' : ''} onClick={() => setTab('favourite')}>♡ Favourites</button><button className={tab === 'left' ? 'active' : ''} onClick={() => setTab('left')}>⌁ For later</button></div>
    {error && <div className="notice error">{error}</div>}{loading && !page && <div className="loading">Loading shortlist…</div>}
    {page && <><p className="result-count">{page.total} {tab === 'favourite' ? 'favourites' : 'saved for later'}</p>{page.total ? <ListingGrid listings={page.items} /> : <div className="empty"><h2>Nothing here yet</h2><p>Properties you save will appear here.</p></div>}{page.next_offset !== null && <button className="button secondary load-more" disabled={loading} onClick={loadMore}>Load more</button>}</>}
  </section>
}

function BrowsePage({ filters, revision }: { filters: Filters; revision: number }) {
  const [params, setParams] = useSearchParams()
  const status = params.get('status') || ''
  const commuteAtMost = params.get('commute_at_most') || ''
  const matchingOnly = params.get('matching_only') === 'true'
  const updateBrowseFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
  }
  const query = queryFor(filters, {
    ...(status ? { status } : {}),
    ...(commuteAtMost ? { commute_at_most: commuteAtMost, sort: 'commute' } : {}),
    ...(matchingOnly ? { matching_only: 'true' } : {}),
  })
  const { page, loading, error, loadMore } = usePagedListings(query, revision)
  useListPositionRestoration(page, loading, loadMore)
  return <section className="page"><div className="section-intro"><p className="eyebrow">Everything in one place</p><h1>Explore all alerts.</h1><p>See homes even when they sit outside your criteria.</p></div>
    <div className="browse-toolbar"><label>Decision <select value={status} onChange={event => updateBrowseFilter('status', event.target.value)}><option value="">All decisions</option><option value="reviewed">Any reviewed</option>{Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label>Max transit commute <input type="number" min="1" placeholder="Any duration" value={commuteAtMost} onChange={event => updateBrowseFilter('commute_at_most', event.target.value)} /></label>
      <label className="browse-checkbox"><input type="checkbox" checked={matchingOnly} onChange={event => updateBrowseFilter('matching_only', event.target.checked ? 'true' : '')} /> Match saved search</label>
      <p className="result-count">{page?.total ?? '—'} homes</p></div>
    {commuteAtMost && <p className="subtle">Homes without a calculated transit commute are excluded by this limit.</p>}
    {error && <div className="notice error">{error}</div>}{loading && !page && <div className="loading">Loading alerts…</div>}
    {page && <>{page.total ? <ListingGrid listings={page.items} /> : <div className="empty"><h2>No alerts found</h2><p>Try a longer date range or a different status.</p></div>}{page.next_offset !== null && <button className="button secondary load-more" disabled={loading} onClick={loadMore}>Load more</button>}</>}
  </section>
}

function MapPage({ filters, revision }: { filters: Filters; revision: number }) {
  const location = useLocation()
  const [params, setParams] = useSearchParams()
  const matchingOnly = params.get('map_matches') === 'true'
  const setMatchingOnly = (value: boolean) => {
    const next = new URLSearchParams(params)
    if (value) next.set('map_matches', 'true')
    else next.delete('map_matches')
    setParams(next)
  }
  const query = queryFor(filters, matchingOnly ? { matching_only: 'true' } : {})
  const { page, loading, error, loadMore } = usePagedListings(query, revision)
  useListPositionRestoration(page, loading, loadMore)
  const [selectedId, setSelectedId] = useState<number | null>(null)
  useEffect(() => setSelectedId(null), [query])
  const onSelect = useCallback((id: number) => setSelectedId(id), [])
  const mapped = page?.items.filter(listing => listing.latitude !== null && listing.longitude !== null) || []
  const selected = page?.items.find(listing => listing.id === selectedId)
  return <section className="page map-page"><div className="section-intro"><p className="eyebrow">Find your corner of London</p><h1>See the bigger picture.</h1><p>Explore homes in your selected date range, or narrow the map to your matches.</p></div>
    <div className="segmented map-scope" aria-label="Map listing scope"><button aria-pressed={!matchingOnly} className={!matchingOnly ? 'active' : ''} onClick={() => setMatchingOnly(false)}>All listings</button><button aria-pressed={matchingOnly} className={matchingOnly ? 'active' : ''} onClick={() => setMatchingOnly(true)}>Matches only</button></div>
    {error && <div className="notice error">{error}</div>}{loading && !page && <div className="loading">Placing homes on the map…</div>}
    {page && <><p className="result-count">{page.total} {matchingOnly ? 'matches' : 'recent homes'} · {mapped.length} plotted · {page.items.length - mapped.length} without coordinates{page.next_offset !== null ? ' · more to load' : ''}</p>
      <div className="map-layout"><div className="map-wrap"><Suspense fallback={<div className="loading">Loading map…</div>}><MapView listings={mapped} selectedId={selectedId} onSelect={onSelect} /></Suspense>{selected && <div className="map-selected"><strong>{selected.title}</strong><span>{rent(selected.monthly_rent)} · <Link to={`/listing/${selected.id}${location.search}`} state={{ from: location.pathname + location.search }} onClick={() => rememberListPosition(location.pathname + location.search, page.items.length)}>View details ↗</Link></span><small>Calculated commutes below ↓</small></div>}</div>
      <div className="map-list">{page.items.map(listing => <button className={`map-listing ${selectedId === listing.id ? 'selected' : ''}`} key={listing.id} onClick={() => onSelect(listing.id)}><Photo listing={listing} /><span><strong>{listing.title}</strong><small>{rent(listing.monthly_rent)} · {listing.assessment.matches_filters ? 'Matches search' : 'Outside filters'}</small></span></button>)}
        {page.next_offset !== null && <button className="button secondary load-more" disabled={loading} onClick={loadMore}>Load more</button>}</div></div>
      {selected ? <div className="map-commutes"><p className="eyebrow">Selected home · {selected.title}</p><CommuteBreakdown listing={selected} /></div> : <div className="map-commutes empty"><p>Select a home on the map or in the list to compare its calculated commutes.</p></div>}
      {!mapped.length && <p className="notice warn">No visible homes have coordinates yet. They remain available in Browse.</p>}</>}
  </section>
}

export default function App() {
  const { filters, update } = useFilters()
  const location = useLocation()
  const [revision, setRevision] = useState(0)
  const query = queryFor(filters)
  const { value: summary } = useResource(() => api.summary(query), `${query}:${revision}`)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const onChanged = () => setRevision(value => value + 1)
  return <><header className="site-header"><div className="header-inner"><Link className="brand" to={`/?${query}`}><span className="brand-mark">⌂</span><span>good<span className="brand-accent">nest</span><small>London rental intelligence</small></span></Link>
      <nav className="desktop-nav" aria-label="Main navigation">{navigation.map(item => <NavLink key={item.to} end={item.to === '/'} to={`${item.to}?${query}`} className={({ isActive }) => isActive ? 'active' : ''}>{item.label}</NavLink>)}</nav>
      <button className="filter-toggle" onClick={() => setFiltersOpen(value => !value)} aria-expanded={filtersOpen}>⚙ <span>Filters</span></button></div></header>
    {filtersOpen && <div className="filter-panel"><div className="filter-inner"><div><p className="eyebrow">Fine-tune your search</p><p>Blank fields use your saved criteria; enter 0 for no limit.</p></div>
      <label>Look back <select value={filters.days} onChange={event => update('days', event.target.value)}><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="90">90 days</option><option value="365">1 year</option></select></label>
      <label>Max rent <input type="number" min="0" placeholder="Saved limit" value={filters.max_rent} onChange={event => update('max_rent', event.target.value)} /></label>
      <label>Min beds <input type="number" min="0" placeholder="Saved min" value={filters.min_bedrooms} onChange={event => update('min_bedrooms', event.target.value)} /></label>
      <label>Areas <input placeholder="e.g. Islington, Hackney" value={filters.areas} onChange={event => update('areas', event.target.value)} /></label>
      <label>Max commute <input type="number" min="0" placeholder="Saved limit" value={filters.max_commute} onChange={event => update('max_commute', event.target.value)} /></label>
      <button className="button primary" onClick={() => setFiltersOpen(false)}>Done</button></div></div>}
    <main key={location.pathname}><Routes><Route path="/" element={<ReviewPage filters={filters} revision={revision} onChanged={onChanged} />} /><Route path="/map" element={<MapPage filters={filters} revision={revision} />} /><Route path="/saved" element={<SavedPage filters={filters} revision={revision} />} /><Route path="/browse" element={<BrowsePage filters={filters} revision={revision} />} /><Route path="/listing/:id" element={<DetailPage filters={filters} onChanged={onChanged} />} /></Routes></main>
    <footer className="site-footer"><span>goodnest · Private rental intelligence</span><span>{summary ? `${summary.matched} matches · ${summary.awaiting_review} to review · ${summary.favourites} favourites` : 'Loading your search…'}</span></footer>
    <nav className="mobile-nav" aria-label="Main navigation">{navigation.map(item => <NavLink key={item.to} end={item.to === '/'} to={`${item.to}?${query}`} className={({ isActive }) => isActive ? 'active' : ''}><span aria-hidden="true">{item.icon}</span><small>{item.label}</small></NavLink>)}</nav>
  </>
}
