import express from 'express';
import path from 'path';
import { fileURLToPath } from 'url';
import { createClient } from 'hafas-client';
import { profile as avvProfile } from 'hafas-client/p/avv/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;
const HOST = process.env.HOST || '0.0.0.0';

// hafas-client has no dedicated VRR profile here; AVV currently resolves VRR stops reliably in this container.
const hafas = createClient(avvProfile, 'vrr-monitor-local');

const CORE_CITY_HINTS = [
  'Bottrop',
  'Oberhausen',
  'Essen',
  'Düsseldorf',
  'Duisburg',
  'Mülheim an der Ruhr',
  'Gelsenkirchen',
  'Gladbeck',
  'Ratingen',
  'Velbert',
  'Heiligenhaus',
  'Mettmann'
];

const CORE_CITY_NAMES = CORE_CITY_HINTS.map(normalizeSearchText);

function isStopLike(location) {
  return location?.type === 'stop' || location?.type === 'station';
}

function locationKey(location) {
  return location?.id || location?.ids?.ifopt || `${location?.name}-${location?.location?.latitude}-${location?.location?.longitude}`;
}

function coordinatesFor(location) {
  return {
    latitude: location?.location?.latitude ?? location?.latitude,
    longitude: location?.location?.longitude ?? location?.longitude
  };
}

function isLikelyVrrArea(location) {
  const { latitude, longitude } = coordinatesFor(location);
  if (typeof latitude !== 'number' || typeof longitude !== 'number') return true;
  return latitude >= 50.85 && latitude <= 51.75 && longitude >= 6.55 && longitude <= 7.35;
}

function dedupeLocations(locations) {
  const seen = new Set();
  const unique = [];
  for (const location of locations) {
    const key = locationKey(location);
    if (!key || seen.has(key)) continue;
    seen.add(key);
    unique.push(location);
  }
  return unique;
}

function normalizeSearchText(value) {
  return String(value || '')
    .toLowerCase()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/ß/g, 'ss')
    .replace(/[^a-z0-9]/g, '');
}

function buildSearchVariants(query) {
  const variants = [query];
  const normalizedQuery = normalizeSearchText(query);
  const queryAlreadyHasCity = CORE_CITY_NAMES.some(city => normalizedQuery.includes(city));
  if (!queryAlreadyHasCity) {
    for (const city of CORE_CITY_HINTS) {
      variants.push(`${query} ${city}`);
      variants.push(`${city} ${query}`);
    }
  }
  return [...new Set(variants)].slice(0, 25);
}

function rankLocation(location, query) {
  const normalizedQuery = normalizeSearchText(query);
  const normalizedName = normalizeSearchText(location.name);
  const exactishNameMatch = normalizedQuery && normalizedName.includes(normalizedQuery);
  const cityMatch = CORE_CITY_NAMES.some(city => normalizedName.includes(city));
  const directPenalty = location.matchSource === 'direct' && exactishNameMatch ? -10 : 0;
  const busPenalty = location.products?.bus === true ? 0 : 10;
  const sourcePenalty = location.matchSource === 'nearby-address' ? 8 : 20;
  const namePenalty = exactishNameMatch ? 0 : sourcePenalty;
  const cityPenalty = cityMatch ? 0 : 4;
  const distancePenalty = Math.min(Math.floor((location.distance || 0) / 100), 9);
  return directPenalty + namePenalty + cityPenalty + busPenalty + distancePenalty;
}

async function searchLocationsVariant(query, variant) {
  try {
    const locations = await hafas.locations(variant, {
      results: 14,
      stops: true,
      addresses: true,
      poi: false
    });

    return (Array.isArray(locations) ? locations : []).map(location => ({
      ...location,
      searchVariant: variant,
      searchQuery: query
    }));
  } catch (error) {
    console.warn(`Could not search "${variant}":`, error.message);
    return [];
  }
}

function addressMatchesQuery(location, query) {
  const normalizedQuery = normalizeSearchText(query);
  const normalizedAddress = normalizeSearchText(location.address || location.name || '');
  return normalizedQuery && normalizedAddress.includes(normalizedQuery);
}

async function nearbyStopsForLocation(location) {
  const { latitude, longitude } = coordinatesFor(location);
  if (typeof latitude !== 'number' || typeof longitude !== 'number') return [];

  try {
    const nearby = await hafas.nearby({
      type: 'location',
      latitude,
      longitude
    }, {
      distance: 900,
      results: 12
    });

    return (Array.isArray(nearby) ? nearby : [])
      .filter(isStopLike)
      .filter(isLikelyVrrArea)
      .map(stop => ({
        ...stop,
        matchSource: 'nearby-address',
        matchedAddress: location.address || location.name || null
      }));
  } catch (error) {
    console.warn('Could not enrich nearby stops:', error.message);
    return [];
  }
}

// Middleware
app.disable('x-powered-by');
app.use(express.json());
app.use((req, res, next) => {
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('Referrer-Policy', 'strict-origin-when-cross-origin');
  res.setHeader('Permissions-Policy', 'geolocation=(self)');
  res.setHeader(
    'Content-Security-Policy',
    [
      "default-src 'self'",
      "script-src 'self' 'unsafe-inline' https://unpkg.com",
      "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
      "font-src 'self' https://fonts.gstatic.com",
      "img-src 'self' data:",
      "connect-src 'self' https://ipapi.co",
      "object-src 'none'",
      "base-uri 'self'",
      "frame-ancestors 'none'"
    ].join('; ')
  );
  next();
});

// Serve static frontend files from 'public' directory robustly
app.use(express.static(path.join(__dirname, 'public')));

app.get('/healthz', (req, res) => {
  res.json({ ok: true, service: 'vrr-monitor' });
});

/**
 * GET /api/locations?q=Suchbegriff
 * Returns up to 5 location results matching the query.
 */
app.get('/api/locations', async (req, res) => {
  const query = String(req.query.q || '').trim();
  if (query.length < 3) {
    return res.status(400).json({ error: 'Query parameter q is required' });
  }
  if (query.length > 80) {
    return res.status(400).json({ error: 'Query is too long' });
  }

  try {
    const variants = buildSearchVariants(query);
    const locations = (await Promise.all(variants.map(variant => searchLocationsVariant(query, variant)))).flat();

    const directStops = locations
      .filter(isStopLike)
      .filter(isLikelyVrrArea)
      .map(stop => ({ ...stop, matchSource: 'direct' }));
    const addressMatches = locations
      .filter(location => !isStopLike(location))
      .filter(location => location.location || (typeof location.latitude === 'number' && typeof location.longitude === 'number'))
      .filter(isLikelyVrrArea)
      .filter(location => location.searchVariant === query || addressMatchesQuery(location, query))
      .slice(0, 5);

    const nearbyStops = (await Promise.all(addressMatches.map(nearbyStopsForLocation))).flat();
    const mergedStops = dedupeLocations([...directStops, ...nearbyStops])
      .sort((a, b) => {
        return rankLocation(a, query) - rankLocation(b, query) || (a.distance || 0) - (b.distance || 0);
      })
      .slice(0, 30);

    res.json(mergedStops);
  } catch (error) {
    console.error('Error fetching locations:', error);
    res.status(500).json({ error: 'Failed to fetch locations from HAFAS API' });
  }
});

/**
 * GET /api/nearby?lat=...&lon=...
 * Finds stations within a 1000m radius of the coordinates.
 */
app.get('/api/nearby', async (req, res) => {
  const { lat, lon } = req.query;
  if (!lat || !lon) {
    return res.status(400).json({ error: 'Latitude (lat) and longitude (lon) are required' });
  }

  try {
    const parsedLat = parseFloat(lat);
    const parsedLon = parseFloat(lon);

    if (
      isNaN(parsedLat) ||
      isNaN(parsedLon) ||
      parsedLat < -90 ||
      parsedLat > 90 ||
      parsedLon < -180 ||
      parsedLon > 180
    ) {
      return res.status(400).json({ error: 'Invalid coordinate values' });
    }

    const nearbyStops = await hafas.nearby({
      type: 'location',
      latitude: parsedLat,
      longitude: parsedLon
    }, {
      distance: 1000
    });

    res.json(nearbyStops);
  } catch (error) {
    console.error('Error fetching nearby stops:', error);
    res.status(500).json({ error: 'Failed to fetch nearby stations from HAFAS API' });
  }
});

/**
 * GET /api/departures?stop=StopID
 * Fetches departures for the next 60 minutes, limited to 15 results.
 */
app.get('/api/departures', async (req, res) => {
  const stopId = String(req.query.stop || '').trim();
  if (!stopId) {
    return res.status(400).json({ error: 'Stop ID (stop) parameter is required' });
  }
  if (!/^[\w:-]{1,64}$/.test(stopId)) {
    return res.status(400).json({ error: 'Invalid stop ID' });
  }

  try {
    const departuresResult = await hafas.departures(stopId, {
      duration: 60
    });

    const departuresList = Array.isArray(departuresResult) 
      ? departuresResult 
      : (departuresResult.departures || []);

    // Capping results to maximum 15 as requested
    const cappedDepartures = departuresList.slice(0, 15);
    res.json(cappedDepartures);
  } catch (error) {
    console.error(`Error fetching departures for stop ${stopId}:`, error);
    res.status(500).json({ error: 'Failed to fetch departures from HAFAS API' });
  }
});

// Start the server
app.listen(PORT, HOST, () => {
  console.log(`====================================================`);
  console.log(`VRR Live-Abfahrtsmonitor running on ${HOST}:${PORT}`);
  console.log(`Open http://localhost:${PORT} in your browser`);
  console.log(`====================================================`);
});
