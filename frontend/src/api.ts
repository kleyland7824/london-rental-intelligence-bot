export type ReviewStatus = 'unreviewed' | 'left' | 'favourite' | 'rejected'

export interface Assessment {
  matches_filters: boolean
  fit_score: number | null
  score_reasons: string[]
  evidence_sources: Record<string, string>
  missing_fields: string[]
  known_fields: number
  exclusion_reasons: string[]
}

export interface RouteInfo {
  destination: string
  mode: string
  minutes: number | null
  url: string | null
}

export interface AIReview {
  summary: string
  recommendation: 'shortlist' | 'maybe' | 'not_suitable'
  strengths: string[]
  concerns: string[]
  unknowns: string[]
  model: string
  reviewed_at: string
}

export interface Listing {
  id: number
  source: string
  url: string | null
  title: string
  address: string | null
  postcode: string | null
  description: string | null
  monthly_rent: number | null
  bedrooms: number | null
  bathrooms: number | null
  available_from: string | null
  availability_note: string | null
  first_seen_at: string
  email_received_at: string | null
  latitude: number | null
  longitude: number | null
  commute_minutes: number | null
  review_status: ReviewStatus
  reviewed_at: string | null
  image_urls: string[]
  routes: RouteInfo[]
  commute_provider: 'google-routes' | 'tfl' | null
  page_reviewed_at: string | null
  ai_review: AIReview | null
  assessment: Assessment
}

export interface ListingPage {
  items: Listing[]
  total: number
  offset: number
  next_offset: number | null
}

export interface Summary {
  imported: number
  matched: number
  awaiting_review: number
  favourites: number
}

export interface Filters {
  days: number
  max_rent: string
  min_bedrooms: string
  areas: string
  max_commute: string
}

export function queryFor(filters: Filters, extra: Record<string, string> = {}): string {
  const params = new URLSearchParams({
    days: String(filters.days),
    ...extra,
  })
  for (const key of ['max_rent', 'min_bedrooms', 'areas', 'max_commute'] as const) {
    if (filters[key] !== '') params.set(key, filters[key])
  }
  return params.toString()
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...options?.headers },
  })
  if (!response.ok) {
    throw new Error(response.status === 404 ? 'Listing not found' : `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  listings: (query: string) => request<ListingPage>(`/listings?${query}`),
  summary: (query: string) => request<Summary>(`/summary?${query}`),
  listing: (id: number, query: string) => request<Listing>(`/listings/${id}?${query}`),
  review: (id: number, status: ReviewStatus) => request<{ id: number; review_status: ReviewStatus; reviewed_at: string }>(
    `/listings/${id}/review`, { method: 'PATCH', body: JSON.stringify({ status }) },
  ),
}

export function rent(value: number | null): string {
  return value === null ? 'Rent unknown' : `£${value.toLocaleString('en-GB')} / mo`
}

export function shortDate(value: string): string {
  return new Date(value).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

export function compareReviewPriority(first: Listing, second: Listing): number {
  const commuteDifference = (first.commute_minutes ?? Infinity) - (second.commute_minutes ?? Infinity)
  if (Number.isFinite(commuteDifference) && commuteDifference !== 0) return commuteDifference
  if (first.commute_minutes === null && second.commute_minutes !== null) return 1
  if (second.commute_minutes === null && first.commute_minutes !== null) return -1
  return (second.assessment.fit_score ?? -1) - (first.assessment.fit_score ?? -1)
}
