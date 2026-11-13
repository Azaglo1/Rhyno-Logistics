const countrySelectors = [...document.querySelectorAll('select[data-country-select]')];
const airportSelectors = [...document.querySelectorAll('select[data-airport-select]')];

function showLocationLoadError() {
  [...countrySelectors, ...airportSelectors].forEach((select) => {
    select.disabled = true;
    select.replaceChildren(new Option('Locations unavailable. Reload the page.', ''));
  });
  const results = document.querySelector('#flight-results');
  if (results) results.textContent = 'Airport options could not be loaded. Reload the page and try again.';
}

function populateAirports(countryCode, airportSelect, preferredCode = '') {
  const airports = window.rhynoAirportsByCountry.get(countryCode) || [];
  airportSelect.replaceChildren(new Option(airports.length ? 'Select an airport' : 'No scheduled airports available', ''));
  airportSelect.disabled = airports.length === 0;
  airports.forEach((airport) => {
    const location = airport.city ? `${airport.city} — ` : '';
    airportSelect.add(new Option(`${location}${airport.name} (${airport.code})`, airport.code));
  });
  if (preferredCode && airports.some((airport) => airport.code === preferredCode)) {
    airportSelect.value = preferredCode;
  }
}

async function loadLocationOptions() {
  if (!countrySelectors.length && !airportSelectors.length) return;

  try {
    const response = await fetch('/assets/data/location-data.json', { credentials: 'same-origin' });
    if (!response.ok) throw new Error(`Location data request failed (${response.status}).`);
    const data = await response.json();
    if (!Array.isArray(data.countries) || !Array.isArray(data.airports)) {
      throw new Error('Location data is incomplete.');
    }

    const countries = data.countries;
    const airportsByCountry = new Map();
    data.airports.forEach((airport) => {
      const airports = airportsByCountry.get(airport.countryCode) || [];
      airports.push(airport);
      airportsByCountry.set(airport.countryCode, airports);
    });
    window.rhynoAirportsByCountry = airportsByCountry;

    countrySelectors.forEach((select) => {
      const flightCountry = select.hasAttribute('data-airport-country');
      const options = flightCountry
        ? countries.filter((country) => airportsByCountry.has(country.code))
        : countries;
      select.replaceChildren(new Option(select.dataset.placeholder || 'Select a country', ''));
      options.forEach((country) => {
        const value = select.dataset.countryValue === 'name' ? country.name : country.code;
        select.add(new Option(country.name, value));
      });
      if (select.dataset.defaultCountry) select.value = select.dataset.defaultCountry;
    });

    airportSelectors.forEach((select) => {
      const countrySelect = document.getElementById(select.dataset.countrySelect);
      if (!countrySelect) throw new Error(`Missing country selector for ${select.id}.`);
      countrySelect.addEventListener('change', () => populateAirports(countrySelect.value, select));
      populateAirports(countrySelect.value, select, select.dataset.defaultAirport);
    });
  } catch (error) {
    console.error('Unable to load country and airport options.', error);
    showLocationLoadError();
  }
}

loadLocationOptions();
