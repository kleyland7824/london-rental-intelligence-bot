import { useEffect, useRef } from 'react'
import * as maplibregl from 'maplibre-gl'
import type { Listing } from './api'

interface Props {
  listings: Listing[]
  selectedId: number | null
  onSelect: (id: number) => void
}

export default function MapView({ listings, selectedId, onSelect }: Props) {
  const container = useRef<HTMLDivElement>(null)
  const map = useRef<maplibregl.Map | null>(null)
  const markers = useRef<maplibregl.Marker[]>([])

  useEffect(() => {
    if (!container.current || map.current) return
    map.current = new maplibregl.Map({
      container: container.current,
      center: [-0.12, 51.51],
      zoom: 10.3,
      style: {
        version: 8,
        sources: {
          osm: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© OpenStreetMap contributors',
          },
        },
        layers: [{ id: 'osm', type: 'raster', source: 'osm' }],
      },
    })
    map.current.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right')
    return () => { map.current?.remove(); map.current = null }
  }, [])

  useEffect(() => {
    if (!map.current) return
    markers.current.forEach(marker => marker.remove())
    markers.current = listings.flatMap(listing => {
      if (listing.latitude === null || listing.longitude === null) return []
      const button = document.createElement('button')
      button.type = 'button'
      button.className = `map-marker status-${listing.review_status} ${listing.assessment.matches_filters ? '' : 'outside-filters'} ${selectedId === listing.id ? 'selected' : ''}`
      button.textContent = listing.monthly_rent === null ? '•' : `£${Math.round(listing.monthly_rent / 100) / 10}k`
      button.setAttribute('aria-label', `Show ${listing.title}`)
      button.addEventListener('click', () => onSelect(listing.id))
      return [new maplibregl.Marker({ element: button })
        .setLngLat([listing.longitude, listing.latitude])
        .addTo(map.current!)]
    })
  }, [listings, selectedId, onSelect])

  useEffect(() => {
    const selected = listings.find(listing => listing.id === selectedId)
    if (selected?.latitude != null && selected.longitude != null) {
      map.current?.flyTo({ center: [selected.longitude, selected.latitude], zoom: Math.max(map.current.getZoom(), 12) })
    }
  }, [listings, selectedId])

  return <div className="map-canvas" ref={container} role="region" aria-label="Map of rental listings" />
}
