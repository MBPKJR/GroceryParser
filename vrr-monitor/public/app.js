/**
 * VRR Live-Abfahrtsmonitor - Frontend Application Logic
 */

// Application State
let activeStopId = null;
let activeStopName = '';
let refreshIntervalTimer = null;
let minuteTickTimer = null;
let favorites = JSON.parse(localStorage.getItem('vrr_favorites')) || [];
let rawDepartures = [];
let activeFilter = 'all';
let lastFetchTime = null;

// DOM Elements
const searchInput = document.getElementById('search-input');
const clearSearchBtn = document.getElementById('clear-search-btn');
const autocompleteList = document.getElementById('autocomplete-list');
const locateBtn = document.getElementById('locate-btn');
const activeStopBanner = document.getElementById('active-stop-banner');
const activeStopNameText = document.getElementById('active-stop-name');
const activeStopDistanceText = document.getElementById('active-stop-distance');
const refreshBtn = document.getElementById('refresh-btn');
const refreshIcon = document.getElementById('refresh-icon');
const departuresList = document.getElementById('departures-list');
const departuresLoader = document.getElementById('departures-loader');
const departuresEmpty = document.getElementById('departures-empty');
const lastUpdateText = document.getElementById('last-update');

// -------------------------------------------------------------
// Utilities
// -------------------------------------------------------------

function debounce(func, delay) {
  let debounceTimer;
  return function() {
    const context = this;
    const args = arguments;
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(() => func.apply(context, args), delay);
  };
}

function formatTime(isoString) {
  if (!isoString) return '--:--';
  const date = new Date(isoString);
  return date.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' });
}

/**
 * Returns minutes until given ISO timestamp (negative = already departed)
 */
function getMinutesUntil(isoString) {
  if (!isoString) return null;
  return Math.round((new Date(isoString) - Date.now()) / 60000);
}

/**
 * Renders the "in X min" / "jetzt" / "HH:MM" primary display string
 */
function formatCountdown(dep) {
  if (dep.cancelled) return { primary: 'Ausfall', cls: 'time-cancelled' };

  const effectiveTime = dep.when || dep.plannedWhen;
  const mins = getMinutesUntil(effectiveTime);

  if (mins === null) return { primary: '--:--', cls: '' };
  if (mins < -1) return { primary: 'abgef.', cls: 'time-departed' };
  if (mins <= 0) return { primary: 'jetzt', cls: 'time-now' };
  if (mins <= 99) return { primary: `${mins} min`, cls: mins <= 5 ? 'time-soon' : '' };
  return { primary: formatTime(effectiveTime), cls: '' };
}

/**
 * Maps HAFAS line to CSS badge class
 */
function getTransitBadgeClass(line) {
  if (!line) return 'badge-train';
  const product = line.product || '';
  const name = (line.name || '').toUpperCase().trim();

  if (product === 'suburban' || (product !== 'subway' && name.match(/^S\d/))) return 'badge-sbahn';
  if (product === 'subway' || name.match(/^U\d/)) return 'badge-ubahn';
  if (product === 'tram') return 'badge-tram';
  if (product === 'bus' || product === 'on-call' || /^\d{3}$/.test(name) || name.startsWith('SB') || name.startsWith('NE') || name.startsWith('AST')) return 'badge-bus';
  return 'badge-train';
}

/**
 * Returns human-readable transit type label from HAFAS product
 */
function getTransitLabel(line) {
  if (!line) return 'Bahn';
  switch (line.product) {
    case 'bus': return 'Bus';
    case 'tram': return 'Tram';
    case 'subway': return 'U-Bahn';
    case 'suburban': return 'S-Bahn';
    case 'regional': return 'Regionalzug';
    case 'national': case 'national-express': return 'Fernzug';
    default: return 'Bahn';
  }
}

function sanitizeStationName(name) {
  if (!name) return '';
  return name
    .replace(/, Bahnhof/gi, '')
    .replace(/ Bf$/gi, '')
    .replace(/ Hbf$/gi, ' Hbf');
}

// -------------------------------------------------------------
// Toast Notification (replaces alert())
// -------------------------------------------------------------

function showToast(message, type = 'info') {
  let toastContainer = document.getElementById('toast-container');
  if (!toastContainer) {
    toastContainer = document.createElement('div');
    toastContainer.id = 'toast-container';
    document.body.appendChild(toastContainer);
  }

  const toast = document.createElement('div');
  toast.className = `toast toast-${type}`;
  toast.textContent = message;
  toastContainer.appendChild(toast);

  // Trigger animation
  requestAnimationFrame(() => toast.classList.add('toast-visible'));

  setTimeout(() => {
    toast.classList.remove('toast-visible');
    setTimeout(() => toast.remove(), 300);
  }, 4000);
}

// -------------------------------------------------------------
// API Actions
// -------------------------------------------------------------

async function fetchSuggestions(query) {
  if (!query || query.trim().length < 3) {
    autocompleteList.style.display = 'none';
    return;
  }

  try {
    const response = await fetch(`/api/locations?q=${encodeURIComponent(query)}`);
    if (!response.ok) throw new Error('API failure');
    const locations = await response.json();
    renderAutocomplete(locations);
  } catch (error) {
    console.error('Error fetching suggestions:', error);
  }
}

async function fetchDepartures() {
  if (!activeStopId) return;

  departuresList.style.display = 'none';
  departuresEmpty.style.display = 'none';
  departuresLoader.style.display = 'flex';
  refreshIcon.classList.add('spinning');

  try {
    const response = await fetch(`/api/departures?stop=${encodeURIComponent(activeStopId)}`);
    if (!response.ok) throw new Error('Failed to fetch departures');
    rawDepartures = await response.json();
    lastFetchTime = Date.now();
    renderDepartures(rawDepartures);
    startMinuteTick();
  } catch (error) {
    console.error('Error fetching departures:', error);
    departuresLoader.style.display = 'none';
    departuresEmpty.style.display = 'flex';
    departuresEmpty.querySelector('p').textContent = 'Fehler beim Laden. Bitte erneut versuchen.';
  } finally {
    refreshIcon.classList.remove('spinning');
  }
}

async function fetchNearbyStops(lat, lon) {
  try {
    const response = await fetch(`/api/nearby?lat=${lat}&lon=${lon}`);
    if (!response.ok) throw new Error('Failed to query nearby stops');
    const stops = await response.json();

    if (stops && stops.length > 0) {
      const closestStop = stops[0];
      if (closestStop.distance) {
        activeStopDistanceText.textContent = `ca. ${closestStop.distance} m entfernt`;
        activeStopDistanceText.style.display = 'block';
      } else {
        activeStopDistanceText.style.display = 'none';
      }
      selectStation(closestStop.id, closestStop.name);
    } else {
      showToast('Keine Haltestellen im Umkreis von 1 km gefunden.', 'warning');
    }
  } catch (error) {
    console.error('Error querying nearby stops:', error);
    showToast('Fehler beim Abrufen der nahen Haltestellen.', 'error');
  } finally {
    resetLocateButton();
  }
}

// -------------------------------------------------------------
// UI Rendering
// -------------------------------------------------------------

function renderAutocomplete(locations) {
  autocompleteList.innerHTML = '';
  const stations = (Array.isArray(locations) ? locations : []).filter(
    loc => loc.type === 'stop' || loc.type === 'station'
  );

  if (stations.length === 0) {
    autocompleteList.style.display = 'none';
    return;
  }

  stations.forEach(station => {
    const item = document.createElement('div');
    item.className = 'autocomplete-item';

    let iconName = 'bus';
    if (station.products) {
      if (station.products.suburban) iconName = 'train-front';
      else if (station.products.subway) iconName = 'train-front';
      else if (station.products.tram) iconName = 'cable-car';
    }

    const cleanName = sanitizeStationName(station.name);
    item.innerHTML = `<i data-lucide="${iconName}"></i><span class="autocomplete-item-name">${cleanName}</span>`;

    item.addEventListener('click', () => {
      activeStopDistanceText.style.display = 'none';
      selectStation(station.id, station.name);
      autocompleteList.style.display = 'none';
      searchInput.value = cleanName;
      clearSearchBtn.style.display = 'flex';
    });

    autocompleteList.appendChild(item);
  });

  lucide.createIcons();
  autocompleteList.style.display = 'block';
}

async function loadItinerary(tripId, container) {
  if (container.querySelector('.itinerary-timeline')) return;

  try {
    const response = await fetch(`/api/trip?id=${encodeURIComponent(tripId)}`);
    if (!response.ok) throw new Error('Trip details failed');
    const trip = await response.json();
    
    if (!trip.stopovers || trip.stopovers.length === 0) {
      container.innerHTML = '<div class="timeline-loader"><i data-lucide="info"></i><span>Kein Fahrtverlauf verfügbar</span></div>';
      lucide.createIcons({ el: container });
      return;
    }

    let html = `
      <div class="itinerary-timeline">
        <div class="timeline-title">
          <i data-lucide="milestone"></i>
          <span>Fahrtverlauf: ${trip.line?.name || ''}</span>
        </div>
    `;

    const activeIndex = trip.stopovers.findIndex(s => {
      const stopIdStr = String(s.stop?.id || '');
      const activeIdStr = String(activeStopId || '');
      return stopIdStr === activeIdStr || sanitizeStationName(s.stop?.name) === sanitizeStationName(activeStopName);
    });

    trip.stopovers.forEach((s, idx) => {
      const isCurrent = idx === activeIndex;
      const isPassed = activeIndex !== -1 && idx < activeIndex;
      
      const timeStr = s.departure || s.arrival || s.plannedDeparture || s.plannedArrival;
      const plannedTimeStr = s.plannedDeparture || s.plannedArrival;
      
      const displayTime = timeStr ? formatTime(timeStr) : '--:--';
      
      let delayHtml = '';
      let delayMin = 0;
      if (s.departureDelay !== null && s.departureDelay !== undefined) {
        delayMin = Math.round(s.departureDelay / 60);
      } else if (s.arrivalDelay !== null && s.arrivalDelay !== undefined) {
        delayMin = Math.round(s.arrivalDelay / 60);
      } else if (timeStr && plannedTimeStr) {
        delayMin = Math.round((new Date(timeStr) - new Date(plannedTimeStr)) / 60000);
      }

      if (delayMin > 0) {
        delayHtml = `<span class="timeline-delay-tag delayed">+${delayMin}</span>`;
      } else if (delayMin === 0 && (s.departureDelay !== null || s.arrivalDelay !== null)) {
        delayHtml = `<span class="timeline-delay-tag ontime">pünktlich</span>`;
      }

      const stopName = sanitizeStationName(s.stop?.name);

      html += `
        <div class="timeline-stop ${isPassed ? 'passed' : ''} ${isCurrent ? 'active-stop' : ''}">
          <div class="time-column">${displayTime}</div>
          <div class="node-column">
            <div class="node-circle"></div>
            <div class="node-line"></div>
          </div>
          <div class="station-column">
            <span>${stopName}</span>
            ${delayHtml}
          </div>
        </div>
      `;
    });

    html += '</div>';
    container.innerHTML = html;
    lucide.createIcons({ el: container });
  } catch (err) {
    console.error('Error loading itinerary:', err);
    container.innerHTML = '<div class="timeline-loader"><i data-lucide="alert-triangle"></i><span>Fahrtverlauf konnte nicht geladen werden</span></div>';
    lucide.createIcons({ el: container });
  }
}

function renderDepartures(departures) {
  departuresLoader.style.display = 'none';
  departuresList.innerHTML = '';

  const filterBar = document.getElementById('transit-filter-bar');
  if (filterBar) filterBar.style.display = 'flex';

  // Apply active filter
  let filteredList = departures;
  if (activeFilter !== 'all') {
    filteredList = departures.filter(dep => {
      if (!dep.line) return false;
      const product = dep.line.product || '';
      const name = (dep.line.name || '').toUpperCase().trim();

      if (activeFilter === 'train') return ['regional', 'national', 'national-express'].includes(product);
      if (activeFilter === 'suburban') return product === 'suburban' || product === 'subway';
      if (activeFilter === 'tram') return product === 'tram';
      if (activeFilter === 'bus') return product === 'bus' || product === 'on-call';
      return true;
    });
  }

  if (!filteredList || filteredList.length === 0) {
    departuresEmpty.style.display = 'flex';
    departuresEmpty.querySelector('p').textContent = 'Keine Abfahrten mit dem gewählten Filter.';
    return;
  }

  departuresEmpty.style.display = 'none';

  filteredList.forEach((dep, idx) => {
    const row = document.createElement('div');
    row.className = 'departure-row';
    row.style.setProperty('--i', idx);

    const lineName = dep.line ? (dep.line.name || '--') : '--';
    const badgeClass = getTransitBadgeClass(dep.line);
    const destination = sanitizeStationName(dep.direction || dep.destination?.name || 'Unbekanntes Ziel');
    const isCancelled = dep.cancelled === true;

    // Planned clock time (always shown as secondary)
    const plannedTimeStr = formatTime(dep.plannedWhen);

    // Primary countdown display
    const countdown = formatCountdown(dep);

    // Delay
    let delayMin = 0;
    if (dep.delay !== undefined && dep.delay !== null) {
      delayMin = Math.round(dep.delay / 60);
    } else if (dep.when && dep.plannedWhen) {
      delayMin = Math.round((new Date(dep.when) - new Date(dep.plannedWhen)) / 60000);
    }

    let delayHtml = '';
    if (isCancelled) {
      delayHtml = '';
    } else if (delayMin > 0) {
      delayHtml = `<span class="delay-badge">+${delayMin}</span>`;
    }

    const platform = dep.platform || dep.plannedPlatform;
    const platformHtml = platform ? `<div class="detail-separator"></div><span>Gl. ${platform}</span>` : '';

    const transitLabel = getTransitLabel(dep.line);

    // Expandable details
    const operatorName = dep.line?.operator?.name || 'Verkehrsverbund';
    const fahrtNr = dep.line?.fahrtNr ? `Fahrt-Nr. ${dep.line.fahrtNr}` : '';

    let remarksHtml = '';
    if (dep.remarks && dep.remarks.length > 0) {
      const hintTexts = dep.remarks
        .filter(r => r.text)
        .map(r => `<div class="expand-remark-pill"><i data-lucide="info"></i><span>${r.text}</span></div>`)
        .join('');
      if (hintTexts) remarksHtml = `<div class="expand-remarks-list">${hintTexts}</div>`;
    }

    row.innerHTML = `
      <div class="line-badge-container">
        <span class="line-badge ${badgeClass}">${lineName}</span>
      </div>
      <div class="departure-main">
        <span class="destination${isCancelled ? ' cancelled-text' : ''}">${destination}</span>
        <div class="departure-details">
          <span>${transitLabel}</span>
          ${platformHtml}
        </div>
      </div>
      <div class="departure-time-block">
        <span class="countdown-time ${countdown.cls}">${countdown.primary}</span>
        <div class="time-subline">
          <span class="clock-time${isCancelled ? ' cancelled-text' : ''}">${plannedTimeStr}</span>
          ${delayHtml}
        </div>
      </div>
      <div class="departure-expand-details" style="display: none;">
        <div class="expand-detail-item">
          <i data-lucide="building-2"></i>
          <span>${operatorName}${fahrtNr ? ` · ${fahrtNr}` : ''}</span>
        </div>
        ${remarksHtml}
        <div class="itinerary-container">
          <div class="timeline-loader">
            <div class="spinner"></div>
            <span>Lade Fahrtverlauf...</span>
          </div>
        </div>
      </div>
    `;

    row.addEventListener('click', () => {
      const details = row.querySelector('.departure-expand-details');
      if (details) {
        const isHidden = details.style.display === 'none';
        details.style.display = isHidden ? 'flex' : 'none';
        if (isHidden) {
          lucide.createIcons({ el: details });
          const itineraryContainer = details.querySelector('.itinerary-container');
          if (itineraryContainer) {
            if (dep.tripId) {
              loadItinerary(dep.tripId, itineraryContainer);
            } else {
              itineraryContainer.innerHTML = '<div class="timeline-loader"><i data-lucide="info"></i><span>Kein Fahrtverlauf verfügbar</span></div>';
              lucide.createIcons({ el: itineraryContainer });
            }
          }
        }
      }
    });

    departuresList.appendChild(row);
  });

  departuresList.style.display = 'flex';

  const now = new Date();
  lastUpdateText.textContent = `Stand: ${now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`;
}

// Re-render countdown numbers every minute without re-fetching
function startMinuteTick() {
  if (minuteTickTimer) clearInterval(minuteTickTimer);
  minuteTickTimer = setInterval(() => {
    if (rawDepartures.length > 0 && departuresList.style.display !== 'none') {
      // Only update time columns, don't rebuild DOM
      const rows = departuresList.querySelectorAll('.departure-row');
      const filtered = getFilteredDepartures();
      rows.forEach((row, i) => {
        const dep = filtered[i];
        if (!dep) return;
        const countdownEl = row.querySelector('.countdown-time');
        const clockEl = row.querySelector('.clock-time');
        if (!countdownEl) return;

        const countdown = formatCountdown(dep);
        countdownEl.textContent = countdown.primary;
        countdownEl.className = `countdown-time ${countdown.cls}`;
      });
    }
  }, 30000);
}

function getFilteredDepartures() {
  if (activeFilter === 'all') return rawDepartures;
  return rawDepartures.filter(dep => {
    if (!dep.line) return false;
    const product = dep.line.product || '';
    if (activeFilter === 'train') return ['regional', 'national', 'national-express'].includes(product);
    if (activeFilter === 'suburban') return product === 'suburban' || product === 'subway';
    if (activeFilter === 'tram') return product === 'tram';
    if (activeFilter === 'bus') return product === 'bus' || product === 'on-call';
    return true;
  });
}

// -------------------------------------------------------------
// Interactive Workflows
// -------------------------------------------------------------

function selectStation(id, name) {
  activeStopId = id;
  activeStopName = sanitizeStationName(name);

  activeStopNameText.textContent = activeStopName;
  updateFavoriteIconState();
  activeStopBanner.style.display = 'flex';

  fetchDepartures();

  if (refreshIntervalTimer) clearInterval(refreshIntervalTimer);
  refreshIntervalTimer = setInterval(fetchDepartures, 30000);
}

function setLocateLoading() {
  locateBtn.disabled = true;
  locateBtn.classList.add('loading');
  locateBtn.querySelector('span').textContent = 'Ortung läuft…';
}

function resetLocateButton() {
  locateBtn.disabled = false;
  locateBtn.classList.remove('loading');
  locateBtn.querySelector('span').textContent = 'Standort';
}

// -------------------------------------------------------------
// Event Listeners
// -------------------------------------------------------------

searchInput.addEventListener('input', debounce((e) => {
  const value = e.target.value;
  clearSearchBtn.style.display = value.trim().length > 0 ? 'flex' : 'none';
  fetchSuggestions(value);
}, 300));

clearSearchBtn.addEventListener('click', () => {
  searchInput.value = '';
  clearSearchBtn.style.display = 'none';
  autocompleteList.style.display = 'none';
  searchInput.focus();
});

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') autocompleteList.style.display = 'none';
});

document.addEventListener('click', (e) => {
  if (!searchInput.contains(e.target) && !autocompleteList.contains(e.target)) {
    autocompleteList.style.display = 'none';
  }
});

async function fallbackIpGeolocation() {
  try {
    const response = await fetch('https://ipapi.co/json/');
    if (!response.ok) throw new Error('IP Geolocation failed');
    const data = await response.json();
    if (data.latitude && data.longitude) {
      activeStopDistanceText.textContent = `Standort via IP (${data.city || 'NRW'})`;
      activeStopDistanceText.style.display = 'block';
      await fetchNearbyStops(data.latitude, data.longitude);
      return;
    }
  } catch (e) {
    console.error('IP Geolocation failed:', e);
  }
  showToast('Standort konnte nicht ermittelt werden. GPS-Zugriff erlauben oder HTTPS nutzen.', 'error');
  resetLocateButton();
}

locateBtn.addEventListener('click', () => {
  setLocateLoading();

  if (!navigator.geolocation) {
    fallbackIpGeolocation();
    return;
  }

  navigator.geolocation.getCurrentPosition(
    (position) => {
      activeStopDistanceText.style.display = 'none';
      fetchNearbyStops(position.coords.latitude, position.coords.longitude);
    },
    (error) => {
      console.warn('GPS failed, trying IP fallback:', error.message);
      fallbackIpGeolocation();
    },
    { enableHighAccuracy: true, timeout: 8000, maximumAge: 0 }
  );
});

// -------------------------------------------------------------
// Favorites
// -------------------------------------------------------------

function renderFavorites() {
  const favoritesSection = document.getElementById('favorites-section');
  const favoritesList = document.getElementById('favorites-list');

  if (!favorites || favorites.length === 0) {
    favoritesSection.style.display = 'none';
    return;
  }

  favoritesList.innerHTML = '';
  favorites.forEach(fav => {
    const pill = document.createElement('button');
    pill.className = 'favorite-pill';
    pill.innerHTML = `<i data-lucide="star"></i><span>${sanitizeStationName(fav.name)}</span>`;

    pill.addEventListener('click', () => {
      activeStopDistanceText.style.display = 'none';
      selectStation(fav.id, fav.name);
    });

    favoritesList.appendChild(pill);
  });

  lucide.createIcons();
  favoritesSection.style.display = 'block';
}

function toggleFavoriteActiveStop() {
  if (!activeStopId || !activeStopName) return;

  const index = favorites.findIndex(fav => fav.id === activeStopId);
  if (index > -1) {
    favorites.splice(index, 1);
  } else {
    favorites.push({ id: activeStopId, name: activeStopName });
  }

  localStorage.setItem('vrr_favorites', JSON.stringify(favorites));
  updateFavoriteIconState();
  renderFavorites();
}

function updateFavoriteIconState() {
  const favoriteIcon = document.getElementById('favorite-icon');
  if (!favoriteIcon) return;
  const isFavorite = favorites.some(fav => fav.id === activeStopId);
  favoriteIcon.classList.toggle('is-favorite', isFavorite);
}

const favToggleBtn = document.getElementById('favorite-toggle-btn');
if (favToggleBtn) favToggleBtn.addEventListener('click', toggleFavoriteActiveStop);

// Filter pills
const filterBar = document.getElementById('transit-filter-bar');
if (filterBar) {
  const pills = filterBar.querySelectorAll('.filter-pill');
  pills.forEach(pill => {
    pill.addEventListener('click', (e) => {
      e.stopPropagation();
      pills.forEach(p => p.classList.remove('active'));
      e.currentTarget.classList.add('active');
      activeFilter = e.currentTarget.getAttribute('data-filter') || 'all';
      renderDepartures(rawDepartures);
    });
  });
}

refreshBtn.addEventListener('click', fetchDepartures);

// Theme Manager
const themes = ['theme-slate', 'theme-amber', 'theme-orange', 'theme-ice'];
let currentTheme = localStorage.getItem('vrr_theme') || 'theme-slate';

function applyTheme(theme) {
  themes.forEach(t => document.body.classList.remove(t));
  document.body.classList.add(theme);
  localStorage.setItem('vrr_theme', theme);
  currentTheme = theme;
}

// Initial application
applyTheme(currentTheme);

const themeBtn = document.getElementById('theme-btn');
if (themeBtn) {
  themeBtn.addEventListener('click', () => {
    const nextIdx = (themes.indexOf(currentTheme) + 1) % themes.length;
    applyTheme(themes[nextIdx]);
  });
}

// Init
renderFavorites();
