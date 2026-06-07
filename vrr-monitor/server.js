import express from 'express';
import cors from 'cors';
import path from 'path';
import { fileURLToPath } from 'url';
import { createClient } from 'hafas-client';
import { profile as dbProfile } from 'hafas-client/p/avv/index.js';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const app = express();
const PORT = process.env.PORT || 3000;

// Initialize HAFAS client for VRR (User Agent: vrr-monitor-local)
const hafas = createClient(dbProfile, 'vrr-monitor-local');

// Middleware
app.use(cors());
app.use(express.json());

// Serve static frontend files from 'public' directory robustly
app.use(express.static(path.join(__dirname, 'public')));

/**
 * GET /api/locations?q=Suchbegriff
 * Returns up to 5 location results matching the query.
 */
app.get('/api/locations', async (req, res) => {
  const query = req.query.q;
  if (!query) {
    return res.status(400).json({ error: 'Query parameter q is required' });
  }

  try {
    const locations = await hafas.locations(query, { results: 5 });
    res.json(locations);
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

    if (isNaN(parsedLat) || isNaN(parsedLon)) {
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
  const stopId = req.query.stop;
  if (!stopId) {
    return res.status(400).json({ error: 'Stop ID (stop) parameter is required' });
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
app.listen(PORT, '0.0.0.0', () => {
  console.log(`====================================================`);
  console.log(`VRR Live-Abfahrtsmonitor running on port ${PORT}`);
  console.log(`Open http://localhost:${PORT} in your browser`);
  console.log(`====================================================`);
});
