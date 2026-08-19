import React, { useState, useRef, useCallback } from 'react';
import { FiMapPin, FiPlus, FiMinus, FiX } from 'react-icons/fi';
import { Link } from 'react-router-dom';
import { useAppContext } from '../App';
import type { Listing } from '../types';

interface MapViewProps {
  listings?: Listing[];
  latitude?: number;
  longitude?: number;
  title?: string;
}

const TILE_SIZE = 256;

function latLngToPixel(lat: number, lng: number, zoom: number): { x: number; y: number } {
  const scale = Math.pow(2, zoom) * TILE_SIZE;
  const x = ((lng + 180) / 360) * scale;
  const latRad = (lat * Math.PI) / 180;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * scale;
  return { x, y };
}

function latLngToTile(lat: number, lng: number, zoom: number): { x: number; y: number } {
  const n = Math.pow(2, zoom);
  const x = Math.floor(((lng + 180) / 360) * n);
  const latRad = (lat * Math.PI) / 180;
  const y = Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n);
  return { x, y };
}

function computeZoomForBounds(minLat: number, maxLat: number, minLng: number, maxLng: number): number {
  const latDiff = maxLat - minLat;
  const lngDiff = maxLng - minLng;
  const maxDiff = Math.max(latDiff, lngDiff);
  if (maxDiff < 0.01) return 14;
  if (maxDiff < 0.05) return 13;
  if (maxDiff < 0.1) return 12;
  if (maxDiff < 0.5) return 10;
  if (maxDiff < 1) return 9;
  if (maxDiff < 5) return 7;
  if (maxDiff < 10) return 6;
  if (maxDiff < 40) return 5;
  return 4;
}

/* ── Locally generated basemap tiles ──────────────────────────────────────────
 * The map background is drawn client-side as deterministic SVG tiles (seeded by
 * tile x/y/zoom) — a stylised street map with land, blocks and roads. No external
 * tile server is contacted (the app is fully self-contained), and the same tile
 * coords always render the same artwork so panning/zooming stays coherent. */
function mulberry32(seed: number) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

const tileCache = new Map<string, string>();

function tileDataUri(x: number, y: number, zoom: number): string {
  const key = `${zoom}/${x}/${y}`;
  const cached = tileCache.get(key);
  if (cached) return cached;
  const rand = mulberry32((x * 73856093) ^ (y * 19349663) ^ (zoom * 83492791));
  const S = 256;
  let body = `<rect width="${S}" height="${S}" fill="#f2efe9"/>`;
  // soft green patches (parks / vegetation)
  const patches = 1 + Math.floor(rand() * 3);
  for (let i = 0; i < patches; i++) {
    const px = rand() * S, py = rand() * S, r = 24 + rand() * 46;
    body += `<circle cx="${px.toFixed(0)}" cy="${py.toFixed(0)}" r="${r.toFixed(0)}" fill="#e0ecd4" opacity="0.8"/>`;
  }
  // occasional water along one edge (coastal feel)
  if (rand() < 0.28) {
    const side = Math.floor(rand() * 4);
    const depth = 40 + rand() * 60;
    const rects = [
      `<rect width="${S}" height="${depth}" y="0" fill="#b7d6e4"/>`,
      `<rect width="${S}" height="${depth}" y="${S - depth}" fill="#b7d6e4"/>`,
      `<rect width="${depth}" height="${S}" x="0" fill="#b7d6e4"/>`,
      `<rect width="${depth}" height="${S}" x="${S - depth}" fill="#b7d6e4"/>`,
    ];
    body += rects[side];
  }
  // building blocks
  const blocks = 5 + Math.floor(rand() * 7);
  for (let i = 0; i < blocks; i++) {
    const bx = rand() * (S - 30), by = rand() * (S - 24);
    const bw = 10 + rand() * 26, bh = 8 + rand() * 20;
    body += `<rect x="${bx.toFixed(0)}" y="${by.toFixed(0)}" width="${bw.toFixed(0)}" height="${bh.toFixed(0)}" fill="#e4dfd3" stroke="#d8d2c4" stroke-width="1"/>`;
  }
  // roads: 2-3 in each direction with light casing, drawn across the tile so they
  // visually continue into neighbours often enough to read as a street grid
  const roads = () => 2 + Math.floor(rand() * 2);
  for (let i = 0, n = roads(); i < n; i++) {
    const yy = (rand() * S).toFixed(0);
    body += `<line x1="0" y1="${yy}" x2="${S}" y2="${yy}" stroke="#d9d3c6" stroke-width="7"/>` +
            `<line x1="0" y1="${yy}" x2="${S}" y2="${yy}" stroke="#ffffff" stroke-width="5"/>`;
  }
  for (let i = 0, n = roads(); i < n; i++) {
    const xx = (rand() * S).toFixed(0);
    body += `<line x1="${xx}" y1="0" x2="${xx}" y2="${S}" stroke="#d9d3c6" stroke-width="7"/>` +
            `<line x1="${xx}" y1="0" x2="${xx}" y2="${S}" stroke="#ffffff" stroke-width="5"/>`;
  }
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${S}" height="${S}">${body}</svg>`;
  const uri = `data:image/svg+xml,${encodeURIComponent(svg)}`;
  tileCache.set(key, uri);
  return uri;
}

function TileGrid({ centerLat, centerLng, zoom, gridSize }: { centerLat: number; centerLng: number; zoom: number; gridSize: number }) {
  const centerTile = latLngToTile(centerLat, centerLng, zoom);
  const offset = Math.floor(gridSize / 2);
  const tiles: Array<{ x: number; y: number }> = [];
  for (let row = 0; row < gridSize; row++) {
    for (let col = 0; col < gridSize; col++) {
      tiles.push({ x: centerTile.x - offset + col, y: centerTile.y - offset + row });
    }
  }

  const gridOriginX = (centerTile.x - offset) * TILE_SIZE;
  const gridOriginY = (centerTile.y - offset) * TILE_SIZE;
  const centerPixel = latLngToPixel(centerLat, centerLng, zoom);
  const gridW = gridSize * TILE_SIZE;
  const gridH = gridSize * TILE_SIZE;
  const withinGridX = centerPixel.x - gridOriginX;
  const withinGridY = centerPixel.y - gridOriginY;

  return (
    <div
      style={{
        position: 'absolute',
        width: gridW,
        height: gridH,
        top: '50%',
        left: '50%',
        transform: `translate(${-withinGridX}px, ${-withinGridY}px)`,
        display: 'grid',
        gridTemplateColumns: `repeat(${gridSize}, ${TILE_SIZE}px)`,
        gridTemplateRows: `repeat(${gridSize}, ${TILE_SIZE}px)`,
      }}
    >
      {tiles.map((tile) => (
        <img
          key={`${zoom}-${tile.x}-${tile.y}`}
          src={tileDataUri(tile.x, tile.y, zoom)}
          alt=""
          style={{ width: TILE_SIZE, height: TILE_SIZE, display: 'block' }}
          loading="eager"
          draggable={false}
        />
      ))}
    </div>
  );
}

export default function MapView({ listings, latitude, longitude, title }: MapViewProps) {
  const { selectedCurrency } = useAppContext();
  const [zoomOverride, setZoomOverride] = useState<number | null>(null);
  const [hoveredMarker, setHoveredMarker] = useState<number | null>(null);
  const [selectedListing, setSelectedListing] = useState<Listing | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const currencySymbol = selectedCurrency?.symbol || '$';
  const rate = selectedCurrency?.exchange_rate || 1;

  // Multi-listing mode
  if (listings && listings.length > 0) {
    const lats = listings.map((l) => l.latitude);
    const lngs = listings.map((l) => l.longitude);
    const bounds = {
      minLat: Math.min(...lats),
      maxLat: Math.max(...lats),
      minLng: Math.min(...lngs),
      maxLng: Math.max(...lngs),
    };

    const autoZoom = computeZoomForBounds(bounds.minLat, bounds.maxLat, bounds.minLng, bounds.maxLng);
    const currentZoom = zoomOverride ?? autoZoom;
    const centerLat = (bounds.minLat + bounds.maxLat) / 2;
    const centerLng = (bounds.minLng + bounds.maxLng) / 2;
    const centerPixel = latLngToPixel(centerLat, centerLng, currentZoom);

    const handleZoomIn = () => setZoomOverride(Math.min((zoomOverride ?? autoZoom) + 1, 18));
    const handleZoomOut = () => setZoomOverride(Math.max((zoomOverride ?? autoZoom) - 1, 2));

    return (
      <div
        ref={containerRef}
        className="relative w-full h-full rounded-xl overflow-hidden cursor-grab"
        style={{ minHeight: 500 }}
        onClick={() => setSelectedListing(null)}
      >
        {/* Tile background */}
        <div className="absolute inset-0 overflow-hidden">
          <TileGrid centerLat={centerLat} centerLng={centerLng} zoom={currentZoom} gridSize={7} />
        </div>

        {/* Markers — positioned using Mercator pixel offsets from center */}
        {listings.map((listing) => {
          const markerPixel = latLngToPixel(listing.latitude, listing.longitude, currentZoom);
          const dx = markerPixel.x - centerPixel.x;
          const dy = markerPixel.y - centerPixel.y;
          const price = Math.round(listing.price_per_night * rate);
          const isHovered = hoveredMarker === listing.id;
          const isSelected = selectedListing?.id === listing.id;

          return (
            <div
              key={listing.id}
              className="absolute cursor-pointer"
              style={{
                left: '50%',
                top: '50%',
                transform: `translate(calc(-50% + ${dx}px), calc(-50% + ${dy}px)) scale(${isHovered ? 1.15 : 1})`,
                zIndex: isSelected ? 30 : isHovered ? 20 : 10,
                transition: 'transform 0.15s ease',
              }}
              onMouseEnter={() => setHoveredMarker(listing.id)}
              onMouseLeave={() => setHoveredMarker(null)}
              onClick={(e) => {
                e.stopPropagation();
                setSelectedListing(selectedListing?.id === listing.id ? null : listing);
              }}
            >
              <div
                className={`text-xs font-semibold px-3 py-1.5 rounded-full shadow-lg whitespace-nowrap transition-colors ${
                  isSelected ? 'bg-black text-white' : 'bg-white text-gray-900 border border-gray-300 hover:bg-gray-900 hover:text-white'
                }`}
              >
                {currencySymbol}{price.toLocaleString()}
              </div>

              {/* Tooltip */}
              {isHovered && !isSelected && (
                <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 bg-gray-900 text-white text-xs font-medium px-3 py-1.5 rounded-lg shadow-md whitespace-nowrap z-40 pointer-events-none max-w-[200px] truncate">
                  {listing.title}
                </div>
              )}

              {/* Popup card */}
              {isSelected && (
                <div
                  className="absolute top-full left-1/2 -translate-x-1/2 mt-2 bg-white rounded-xl shadow-xl overflow-hidden z-40"
                  style={{ width: 260 }}
                  onClick={(e) => e.stopPropagation()}
                >
                  <button
                    onClick={(e) => { e.stopPropagation(); setSelectedListing(null); }}
                    className="absolute top-2 right-2 z-10 w-6 h-6 bg-white/90 rounded-full flex items-center justify-center shadow"
                  >
                    <FiX className="w-3 h-3" />
                  </button>
                  {listing.images && listing.images.length > 0 && (
                    <img
                      src={listing.images[0].url}
                      alt={listing.title}
                      className="w-full h-36 object-cover"
                      loading="eager"
                      onError={(e) => {
                        const target = e.target as HTMLImageElement;
                        target.onerror = null;
                        target.src = 'data:image/svg+xml,<svg xmlns="http://www.w3.org/2000/svg" width="260" height="144" viewBox="0 0 260 144"><rect fill="%23e5e7eb" width="260" height="144"/><text fill="%239ca3af" font-family="sans-serif" font-size="12" text-anchor="middle" x="130" y="76">No image</text></svg>';
                      }}
                    />
                  )}
                  <Link to={`/listings/${listing.id}`} className="block p-3">
                    <h4 className="text-sm font-semibold text-gray-900 truncate">{listing.title}</h4>
                    <p className="text-sm text-gray-600 mt-1">
                      <span className="font-semibold">{currencySymbol}{price.toLocaleString()}</span>
                      <span className="text-gray-400"> / night</span>
                      {listing.avg_rating > 0 && (
                        <span className="ml-2 text-gray-900">★ {listing.avg_rating.toFixed(1)}</span>
                      )}
                    </p>
                  </Link>
                </div>
              )}
            </div>
          );
        })}

        {/* Zoom controls */}
        <div className="absolute bottom-4 right-4 flex flex-col gap-0.5 z-20">
          <button
            onClick={(e) => { e.stopPropagation(); handleZoomIn(); }}
            className="w-8 h-8 bg-white border border-gray-300 rounded-t-lg shadow flex items-center justify-center hover:bg-gray-50 transition"
            aria-label="Zoom in"
          >
            <FiPlus className="w-4 h-4 text-gray-700" />
          </button>
          <button
            onClick={(e) => { e.stopPropagation(); handleZoomOut(); }}
            className="w-8 h-8 bg-white border border-gray-300 rounded-b-lg shadow flex items-center justify-center hover:bg-gray-50 transition"
            aria-label="Zoom out"
          >
            <FiMinus className="w-4 h-4 text-gray-700" />
          </button>
        </div>

        {/* Attribution */}
        <div className="absolute bottom-1 left-1 bg-white/80 rounded px-1.5 py-0.5 z-20">
          <p className="text-[9px] text-gray-500">Map data © Airbnb</p>
        </div>
      </div>
    );
  }

  // Single-listing mode
  const lat = latitude ?? 0;
  const lng = longitude ?? 0;

  return (
    <div className="relative w-full rounded-xl overflow-hidden bg-gray-200" style={{ height: 480 }}>
      <div className="absolute inset-0 overflow-hidden">
        <TileGrid centerLat={lat} centerLng={lng} zoom={14} gridSize={5} />
      </div>

      {/* Center pin */}
      <div className="absolute inset-0 flex flex-col items-center justify-center z-10">
        <div className="bg-rose-500 text-white rounded-full p-3 shadow-lg mb-3">
          <FiMapPin className="w-6 h-6" />
        </div>
        <div className="bg-white/90 backdrop-blur-sm rounded-lg px-4 py-2 shadow-md text-center max-w-xs">
          {title && <p className="text-sm font-semibold text-gray-800">{title}</p>}
          <p className="text-xs text-gray-500 mt-1">Exact location provided after booking</p>
        </div>
      </div>

      <div className="absolute bottom-3 right-3 bg-white/70 backdrop-blur-sm rounded px-2 py-1 z-10">
        <p className="text-[10px] text-gray-400 font-mono">
          {lat.toFixed(4)}°, {lng.toFixed(4)}°
        </p>
      </div>

      <div className="absolute bottom-1 left-1 bg-white/80 rounded px-1.5 py-0.5 z-10">
        <p className="text-[9px] text-gray-500">Map data © Airbnb</p>
      </div>
    </div>
  );
}
