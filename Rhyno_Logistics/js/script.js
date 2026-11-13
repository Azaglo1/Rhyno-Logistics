document.querySelector('#tracking-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const input = document.querySelector('#tracking-input');
  const result = document.querySelector('#tracking-result');
  const details = document.querySelector('#tracking-details');
  const trackingNumber = input.value.trim().toUpperCase();
  input.value = trackingNumber;
  result.textContent = 'Checking the shipment database…';
  details.hidden = true;
  try {
    const data = await apiRequest(`/api/track/${encodeURIComponent(trackingNumber)}`);
    const shipment = data.shipment;
    result.textContent = `Shipment ${trackingNumber} found.`;
    details.hidden = false;
    details.innerHTML = `<div class="tracking-overview"><div><h3>${escapeHtml(trackingNumber)}</h3><p>${escapeHtml(shipment.origin)} to ${escapeHtml(shipment.destination)}</p></div><strong>${escapeHtml(shipment.status)}</strong></div><div class="tracking-meta"><div><small>Current location</small><b>${escapeHtml(shipment.location || 'Not reported')}</b></div><div><small>Estimated delivery</small><b>${shipment.eta ? new Date(shipment.eta).toLocaleString() : 'Not available'}</b></div><div><small>Last updated</small><b>${shipment.updated_at ? new Date(shipment.updated_at).toLocaleString() : 'Not available'}</b></div></div><div class="tracking-history">${data.history.map((entry) => `<div><small>${new Date(entry.occurred_at).toLocaleString()}</small><b>${escapeHtml(entry.status)}</b><p>${escapeHtml(entry.location)}</p></div>`).join('')}</div>`;
  } catch (error) {
    result.textContent = error.message === 'Tracking number not found.' ? 'No shipment was found for that tracking number.' : error.message;
    details.hidden = true;
  }
});

document.querySelector('#contact-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const result = form.querySelector('.form-result');
  result.textContent = 'Sending your enquiry…';
  try {
    const data = await apiRequest('/api/contact', {
      method: 'POST',
      body: JSON.stringify({
        name: document.querySelector('#contact-name').value.trim(),
        email: document.querySelector('#contact-email').value.trim(),
        message: document.querySelector('#contact-message').value.trim()
      })
    });
    result.textContent = data.message;
    result.classList.remove('error');
    form.reset();
  } catch (error) {
    result.textContent = error.message;
    result.classList.add('error');
  }
});

const flightSearchForm = document.querySelector('#flight-search-form');
const flightResults = document.querySelector('#flight-results');
const passengerPanel = document.querySelector('#passenger-panel');
const passengerForm = document.querySelector('#passenger-form');
let selectedFlight = null;

function escapeHtml(value) {
  return String(value).replace(/[&<>'"]/g, (character) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[character]));
}

async function apiRequest(path, options = {}) {
  const tokenResponse = await fetch('/api/csrf-token', { credentials: 'same-origin' });
  const tokenData = await tokenResponse.json();
  const headers = { ...(options.headers || {}), 'X-CSRF-Token': tokenData.token };
  if (!(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}

function selectedCountryName(selector) {
  const select = document.querySelector(selector);
  return select?.selectedOptions[0]?.textContent.trim() || '';
}

function setFlightDates() {
  const departure = document.querySelector('#flight-departure');
  const returnDate = document.querySelector('#flight-return');
  const date = new Date();
  date.setDate(date.getDate() + 14);
  const returnValue = new Date(date);
  returnValue.setDate(returnValue.getDate() + 7);
  const formatDate = (value) => {
    const localDate = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
    return localDate.toISOString().slice(0, 10);
  };
  if (departure) departure.value = formatDate(date);
  if (returnDate) returnDate.value = formatDate(returnValue);
}

function updateReturnDate() {
  const isOneWay = document.querySelector('input[name="trip-type"]:checked')?.value === 'one-way';
  const returnDate = document.querySelector('.return-date');
  const returnInput = document.querySelector('#flight-return');
  if (!returnDate || !returnInput) return;
  returnDate.hidden = isOneWay;
  returnInput.required = !isOneWay;
}

function flightDateTime(value) {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? escapeHtml(value || '') : date.toLocaleString();
}

function renderFlights(offers, origin, destination, passengers, travelClass, tripType, departureDate, returnDate, demoMode) {
  if (!offers.length) {
    flightResults.textContent = 'No flights were returned for this search. Try different airports or dates.';
    return;
  }
  const notice = demoMode
    ? '<strong>Demo data only.</strong> These sample itineraries are fictional. You can submit a test request to Rhyno, but no airline is contacted and no ticket is reserved.'
    : '<strong>Amadeus flight search results.</strong> Prices and availability can change; submitting a request does not reserve a seat or issue a ticket.';
  flightResults.innerHTML = `<p class="demo-notice">${notice}</p><div class="results-heading"><strong>${offers.length} offers available</strong><span>${escapeHtml(origin)} to ${escapeHtml(destination)} · ${escapeHtml(departureDate)}${returnDate ? ` – ${escapeHtml(returnDate)}` : ' · one way'} · ${passengers} passenger${passengers > 1 ? 's' : ''}</span></div>${offers.map((offer, index) => {
    const legs = offer.slices.map((slice) => `<div class="offer-leg"><span><strong>${flightDateTime(slice.departure)}</strong> ${escapeHtml(slice.origin)} → ${escapeHtml(slice.destination)} <strong>${flightDateTime(slice.arrival)}</strong></span><small>${escapeHtml(slice.duration || '')} · ${slice.stops === 0 ? 'Non-stop' : `${slice.stops} stop${slice.stops === 1 ? '' : 's'}`} · ${escapeHtml(slice.flightNumbers.join(', '))}</small></div>`).join('');
    const requestControl = demoMode
      ? `<button class="select-flight" type="button" data-flight="${index}">Submit test request</button>`
      : `<button class="select-flight" type="button" data-flight="${index}">Request this itinerary</button>`;
    const sourceLabel = demoMode ? 'Demo offer' : 'Amadeus offer';
    return `<article class="flight-card"><div class="airline"><span class="airline-logo">${escapeHtml(offer.airlineCode || '✈')}</span><span><strong>${escapeHtml(offer.airline)}</strong><small>${escapeHtml(travelClass)} · ${sourceLabel}</small></span></div><div class="flight-legs">${legs}</div><div class="flight-price"><strong>${escapeHtml(offer.totalCurrency)} ${escapeHtml(offer.totalAmount)}</strong><small>total for ${passengers} passenger${passengers === 1 ? '' : 's'}</small></div>${requestControl}</article>`;
  }).join('')}`;
  flightResults.querySelectorAll('.select-flight').forEach((button) => {
    button.addEventListener('click', () => {
      const offer = offers[Number(button.dataset.flight)];
      selectedFlight = {
        ...offer, offerId: offer.id, provider: demoMode ? 'Demo' : 'Amadeus', demoMode, origin, destination,
        passengers: Number(passengers), travelClass, departureDate, returnDate, tripType
      };
      document.querySelector('#summary-route').textContent = `${origin} to ${destination}`;
      document.querySelector('#summary-flight').textContent = `${offer.airline} · ${travelClass} · ${tripType}${demoMode ? ' · DEMO TEST' : ''}`;
      document.querySelector('#summary-price').textContent = `${offer.totalCurrency} ${offer.totalAmount}`;
      document.querySelector('#summary-travelers').textContent = `${selectedFlight.passengers} traveller${selectedFlight.passengers > 1 ? 's' : ''}`;
      passengerPanel.hidden = false;
      passengerPanel.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  });
}

flightSearchForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const originSelect = document.querySelector('#flight-origin');
  const destinationSelect = document.querySelector('#flight-destination');
  const origin = originSelect.value;
  const destination = destinationSelect.value;
  const originLabel = originSelect.selectedOptions[0]?.textContent.trim() || '';
  const destinationLabel = destinationSelect.selectedOptions[0]?.textContent.trim() || '';
  const departureDate = document.querySelector('#flight-departure').value;
  const returnDate = document.querySelector('#flight-return').value;
  const tripType = document.querySelector('input[name="trip-type"]:checked').value;
  if (!origin || !destination) {
    flightResults.textContent = 'Select a departure and destination airport.';
    return;
  }
  if (origin === destination) {
    flightResults.textContent = 'Departure and destination must be different.';
    return;
  }
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const departure = new Date(`${departureDate}T00:00:00`);
  const returning = returnDate ? new Date(`${returnDate}T00:00:00`) : null;
  if (Number.isNaN(departure.getTime()) || departure < today) {
    flightResults.textContent = 'Departure date must be today or later.';
    return;
  }
  if (tripType === 'round-trip' && (!returning || Number.isNaN(returning.getTime()) || returning < departure)) {
    flightResults.textContent = 'Return date must be on or after departure.';
    return;
  }
  const passengers = Number(document.querySelector('#flight-passengers').value);
  if (!origin || !destination || passengers < 1 || passengers > 9) {
    flightResults.textContent = 'Enter a route and select between 1 and 9 passengers.';
    return;
  }
  const travelClass = document.querySelector('#flight-class').value;
  selectedFlight = null;
  passengerPanel.hidden = true;
  document.querySelector('#booking-confirmation').hidden = true;
  flightResults.textContent = 'Searching flight offers…';
  try {
    const data = await apiRequest('/api/flights/search', {
      method: 'POST',
      body: JSON.stringify({ origin, destination, passengers, travelClass, tripType, departureDate, returnDate })
    });
    renderFlights(data.offers, originLabel, destinationLabel, data.passengerCount, travelClass, tripType, departureDate, tripType === 'round-trip' ? returnDate : '', data.demoMode);
  } catch (error) {
    flightResults.textContent = error.message;
  }
});

document.querySelectorAll('input[name="trip-type"]').forEach((input) => input.addEventListener('change', updateReturnDate));

async function startFlightPayment(button, statusElement) {
  if (!button) return;
  const reference = button.dataset.reference;
  const provider = button.dataset.provider;
  if (statusElement) statusElement.textContent = 'Starting secure payment…';
  button.disabled = true;
  try {
    if (provider === 'paystack') {
      const data = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}/paystack-mobile-money`, {
        method: 'POST', body: JSON.stringify({})
      });
      if (data.checkout_url) {
        window.location.assign(data.checkout_url);
        return;
      }
      if (data.status === 'Paid') {
        if (statusElement) statusElement.textContent = data.message || 'Payment is already complete.';
        return;
      }
      throw new Error('Paystack did not return a checkout link.');
    }
    const data = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}/paypal-order`, {
      method: 'POST', body: JSON.stringify({})
    });
    if (data.approval_url) {
      window.location.assign(data.approval_url);
      return;
    }
    if (data.already_approved && data.order_id) {
      const result = await apiRequest('/api/payments/paypal/capture', {
        method: 'POST', body: JSON.stringify({ order_id: data.order_id })
      });
      if (statusElement) statusElement.textContent = result.message || 'Payment confirmed.';
      return;
    }
    if (data.status === 'Paid') {
      if (statusElement) statusElement.textContent = data.message || 'Payment is already complete.';
      return;
    }
    throw new Error('PayPal did not return a checkout link.');
  } catch (error) {
    if (statusElement) statusElement.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

passengerForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  if (!selectedFlight) return;
  const confirmation = document.querySelector('#booking-confirmation');
  confirmation.hidden = false;
  confirmation.textContent = 'Submitting your request…';
  const name = document.querySelector('#passenger-name').value.trim();
  try {
    const data = await apiRequest('/api/bookings', {
      method: 'POST',
      body: JSON.stringify({
        category: 'flight',
        payload: {
          customer: { name, email: document.querySelector('#passenger-email').value.trim(), phone: document.querySelector('#passenger-phone').value.trim() },
          passenger: { nationality: document.querySelector('#passenger-nationality').value.trim(), details: document.querySelector('#passenger-details').value.trim() },
          flight: selectedFlight
        }
      })
    });
    if (selectedFlight.demoMode) {
      confirmation.innerHTML = `<h3>Demo flight test request saved</h3><p>Thank you, ${escapeHtml(name)}. Your request reference is <strong>${escapeHtml(data.reference)}</strong>.</p><p>${escapeHtml(selectedFlight.origin)} to ${escapeHtml(selectedFlight.destination)} · ${escapeHtml(selectedFlight.tripType)} · ${selectedFlight.passengers} passenger${selectedFlight.passengers === 1 ? '' : 's'}.</p><p>This is a test record using fictional sample data. No airline was contacted and no reservation or ticket was created.</p>`;
    } else {
      const amountLabel = `${escapeHtml(selectedFlight.totalCurrency || '')} ${escapeHtml(selectedFlight.totalAmount || '')}`;
      confirmation.innerHTML = `<h3>Flight booking saved</h3><p>Thank you, ${escapeHtml(name)}. Your request reference is <strong>${escapeHtml(data.reference)}</strong>.</p><p>${escapeHtml(selectedFlight.origin)} to ${escapeHtml(selectedFlight.destination)} · ${escapeHtml(selectedFlight.tripType)} · ${selectedFlight.passengers} passenger${selectedFlight.passengers === 1 ? '' : 's'}.</p><p><strong>Amount due: ${amountLabel}</strong></p><p>Complete payment now to continue with your booking. Mobile money checkout is available for GHS bookings (Paystack).</p><div class="booking-actions flight-pay-actions"><button type="button" class="primary-button pay-flight" data-provider="paypal" data-reference="${escapeHtml(data.reference)}">Pay with PayPal</button><button type="button" class="dark-button pay-flight" data-provider="paystack" data-reference="${escapeHtml(data.reference)}">Pay with mobile money</button></div><p class="booking-lookup-result flight-pay-status" aria-live="polite"></p>`;
      confirmation.querySelectorAll('.pay-flight').forEach((button) => button.addEventListener('click', () => startFlightPayment(button, confirmation.querySelector('.flight-pay-status'))));
    }
    document.querySelector('#booking-reference-input').value = data.reference;
    passengerForm.reset();
    confirmation.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (error) {
    confirmation.textContent = error.message;
  }
});

document.querySelector('#booking-lookup-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const reference = document.querySelector('#booking-reference-input').value.trim().toUpperCase();
  const result = document.querySelector('#booking-lookup-result');
  result.textContent = 'Loading booking…';
  const email = document.querySelector('#booking-email-input')?.value.trim();
  try {
    const booking = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}${email ? `?email=${encodeURIComponent(email)}` : ''}`);
    const record = booking.booking;
    const payload = record.payload || {};
    const summary = payload.flight
      ? `${escapeHtml(payload.flight.origin)} to ${escapeHtml(payload.flight.destination)}`
      : `${escapeHtml(payload.shipment?.origin || payload.cargo?.origin || '')} to ${escapeHtml(payload.shipment?.destination || payload.cargo?.destination || '')}`;
    let paymentHtml = '';
    if (record.category === 'flight' && ['Pending', 'Pending payment', 'Approved'].includes(record.status) && payload.flight?.totalAmount) {
      const currency = String(payload.flight.totalCurrency || '').toUpperCase();
      paymentHtml = `<p><strong>Amount due: ${escapeHtml(currency)} ${escapeHtml(payload.flight.totalAmount)}</strong></p><div class="booking-actions flight-pay-actions"><button type="button" class="primary-button pay-flight" data-provider="paypal" data-reference="${escapeHtml(record.reference)}">Pay with PayPal</button><button type="button" class="dark-button pay-flight" data-provider="paystack" data-reference="${escapeHtml(record.reference)}">Pay with mobile money</button></div><p class="flight-pay-status" aria-live="polite"></p><small>Mobile money (Paystack) is available for GHS payments.</small>`;
    }
    result.innerHTML = `<strong>${escapeHtml(record.reference)}</strong> · ${escapeHtml(record.category)} · ${escapeHtml(record.status)} · ${summary}${paymentHtml}`;
    result.querySelectorAll('.pay-flight').forEach((button) => button.addEventListener('click', () => startFlightPayment(button, result.querySelector('.flight-pay-status'))));
  } catch (error) {
    result.textContent = error.message;
  }
});

setFlightDates();
updateReturnDate();

const parcelForm = document.querySelector('#parcel-form');
const parcelConfirmation = document.querySelector('#parcel-confirmation');

parcelForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const selectedOption = document.querySelector('input[name="delivery-option"]:checked').value;
  const shipment = {
    sender: {
      name: document.querySelector('#sender-name').value.trim(),
      phone: document.querySelector('#sender-phone').value.trim(),
      email: document.querySelector('#sender-email').value.trim(),
      country: selectedCountryName('#sender-country'),
      pickupLocation: document.querySelector('#pickup-location').value.trim(),
      pickupAddress: document.querySelector('#pickup-address').value.trim()
    },
    recipient: {
      name: document.querySelector('#recipient-name').value.trim(),
      phone: document.querySelector('#recipient-phone').value.trim(),
      email: document.querySelector('#recipient-email').value.trim(),
      country: selectedCountryName('#destination-country'),
      address: document.querySelector('#delivery-address').value.trim()
    },
    parcel: {
      description: document.querySelector('#parcel-description').value.trim(),
      category: document.querySelector('#parcel-category').value,
      weight: document.querySelector('#parcel-weight').value,
      dimensions: document.querySelector('#parcel-dimensions').value.trim(),
      quantity: document.querySelector('#parcel-quantity').value,
      deliveryType: document.querySelector('#delivery-type').value,
      instructions: document.querySelector('#parcel-instructions').value.trim()
    },
    option: selectedOption,
  };
  parcelConfirmation.hidden = false;
  parcelConfirmation.textContent = 'Submitting your parcel request…';
  try {
    const data = await apiRequest('/api/bookings', { method: 'POST', body: JSON.stringify({ category: 'parcel', payload: shipment }) });
    parcelConfirmation.innerHTML = `<h3>Parcel request received</h3><p>Thank you, ${escapeHtml(shipment.sender.name)}. Your server booking reference is <strong>${escapeHtml(data.reference)}</strong>.</p><p>Your request is saved for review. Delivery timing and charges will be confirmed by our logistics team.</p>`;
    document.querySelector('#parcel-reference-input').value = data.reference;
    parcelForm.reset();
    parcelConfirmation.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (error) {
    parcelConfirmation.textContent = error.message;
  }
});

document.querySelector('#parcel-lookup-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const reference = document.querySelector('#parcel-reference-input').value.trim().toUpperCase();
  const email = document.querySelector('#parcel-email-input').value.trim();
  const result = document.querySelector('#parcel-status-result');
  try {
    const data = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}?email=${encodeURIComponent(email)}`);
    result.textContent = `${data.booking.reference} · Parcel request status: ${data.booking.status}`;
  } catch (error) {
    result.textContent = error.message;
  }
});

const clearingForm = document.querySelector('#clearing-form');
const clearingConfirmation = document.querySelector('#clearing-confirmation');
const clearingStatuses = ['Pending', 'Documents Received', 'Under Review', 'Processing', 'Cleared', 'Completed'];

function setClearingStatus(status) {
  const statusIndex = clearingStatuses.indexOf(status);
  document.querySelectorAll('.status-steps span').forEach((step, index) => {
    step.classList.toggle('active', index === statusIndex);
  });
}

clearingForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const request = {
    customer: {
      name: document.querySelector('#clearing-name').value.trim(),
      company: document.querySelector('#clearing-company').value.trim(),
      email: document.querySelector('#clearing-email').value.trim(),
      phone: document.querySelector('#clearing-phone').value.trim()
    },
    shipment: {
      direction: document.querySelector('#clearing-direction').value,
      origin: selectedCountryName('#clearing-origin'),
      destination: selectedCountryName('#clearing-destination'),
      port: document.querySelector('#clearing-port').value.trim(),
      description: document.querySelector('#clearing-description').value.trim(),
      category: document.querySelector('#clearing-category').value,
      value: document.querySelector('#clearing-value').value.trim(),
      weight: document.querySelector('#clearing-weight').value,
      method: document.querySelector('#clearing-method').value,
      instructions: document.querySelector('#clearing-instructions').value.trim()
    },
  };
  clearingConfirmation.hidden = false;
  clearingConfirmation.textContent = 'Uploading documents and submitting your request…';
  const formData = new FormData();
  formData.append('category', 'clearing');
  formData.append('payload', JSON.stringify(request));
  formData.append('commercial-invoice', document.querySelector('#commercial-invoice').files[0]);
  formData.append('packing-list', document.querySelector('#packing-list').files[0]);
  [...document.querySelector('#shipping-documents').files].forEach((file) => formData.append('shipping-documents', file));
  try {
    const data = await apiRequest('/api/bookings', { method: 'POST', body: formData });
    clearingConfirmation.innerHTML = `<h3>Clearing request received</h3><p>Thank you, ${escapeHtml(request.customer.name)}. Your request reference is <strong>${escapeHtml(data.reference)}</strong>.</p><p>Documents were uploaded securely to the server. Our team will review your ${escapeHtml(request.shipment.direction.toLowerCase())} shipment through ${escapeHtml(request.shipment.port)}. No payment has been taken.</p>`;
    document.querySelector('#clearing-reference-input').value = data.reference;
    setClearingStatus(data.status);
    clearingForm.reset();
    clearingConfirmation.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (error) {
    clearingConfirmation.textContent = error.message;
  }
});

document.querySelector('#clearing-status-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const reference = document.querySelector('#clearing-reference-input').value.trim().toUpperCase();
  const result = document.querySelector('#clearing-status-result');
  const email = document.querySelector('#clearing-email-input')?.value.trim();
  try {
    const data = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}${email ? `?email=${encodeURIComponent(email)}` : ''}`);
    const booking = data.booking;
    setClearingStatus(booking.status);
    result.textContent = `${booking.reference} · Current status: ${booking.status}`;
  } catch (error) {
    setClearingStatus('Pending');
    result.textContent = error.message;
  }
});

const freightForm = document.querySelector('#freight-form');

function parseDimensions(value) {
  const measurements = value.split(/[x×]/i).map((part) => Number(part.trim()));
  if (measurements.length !== 3 || measurements.some((measurement) => !Number.isFinite(measurement) || measurement <= 0)) return null;
  return measurements[0] * measurements[1] * measurements[2] / 1000000;
}

freightForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const dimensions = document.querySelector('#freight-dimensions').value.trim();
  const volume = parseDimensions(dimensions);
  if (!volume) {
    document.querySelector('#freight-dimensions').setCustomValidity('Enter dimensions as length × width × height in centimetres.');
    freightForm.reportValidity();
    return;
  }
  document.querySelector('#freight-dimensions').setCustomValidity('');
  const request = {
    customer: { name: document.querySelector('#freight-name').value.trim(), company: document.querySelector('#freight-company').value.trim(), email: document.querySelector('#freight-email').value.trim(), phone: document.querySelector('#freight-phone').value.trim() },
    cargo: { origin: `${document.querySelector('#freight-origin').value.trim()}, ${selectedCountryName('#freight-origin-country')}`, destination: `${document.querySelector('#freight-destination').value.trim()}, ${selectedCountryName('#freight-destination-country')}`, type: document.querySelector('#freight-type').value, weight: document.querySelector('#freight-weight').value, dimensions, quantity: document.querySelector('#freight-quantity').value, method: document.querySelector('#freight-method').value, date: document.querySelector('#freight-date').value, handling: document.querySelector('#freight-handling').value.trim(), instructions: document.querySelector('#freight-instructions').value.trim(), volume }
  };
  const summary = document.querySelector('#freight-summary');
  summary.hidden = false;
  summary.textContent = 'Submitting your quotation request…';
  try {
    const data = await apiRequest('/api/bookings', { method: 'POST', body: JSON.stringify({ category: 'freight', payload: request }) });
    summary.innerHTML = `<h3>Quotation request received</h3><p>Thank you, ${escapeHtml(request.customer.name)}. Your server reference is <strong>${escapeHtml(data.reference)}</strong>.</p><p>Shipment summary: ${escapeHtml(request.cargo.type)} · ${escapeHtml(request.cargo.origin)} to ${escapeHtml(request.cargo.destination)} · ${escapeHtml(request.cargo.weight)} kg · ${volume.toFixed(3)} m³ estimated volume.</p><p>Our team will prepare an approved quotation. No estimated freight price or payment has been applied.</p>`;
    document.querySelector('#quotation-reference-input').value = data.reference;
    freightForm.reset();
    summary.scrollIntoView({ behavior: 'smooth', block: 'center' });
  } catch (error) {
    summary.textContent = error.message;
  }
});

document.querySelector('#quotation-status-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const reference = document.querySelector('#quotation-reference-input').value.trim().toUpperCase();
  const result = document.querySelector('#quotation-status-result');
  const email = document.querySelector('#freight-email-input')?.value.trim();
  try {
    const data = await apiRequest(`/api/bookings/${encodeURIComponent(reference)}${email ? `?email=${encodeURIComponent(email)}` : ''}`);
    const booking = data.booking;
    result.textContent = `${booking.reference} · Quotation status: ${booking.status}`;
    if (booking.quotation) {
      const quote = booking.quotation;
      result.textContent += quote.status === 'Approved'
        ? ` · Approved quote: ${quote.currency} ${Number(quote.amount).toFixed(2)}${quote.notes ? ` · ${quote.notes}` : ''}`
        : ` · Quotation: ${quote.status}`;
    }
  } catch (error) {
    result.textContent = error.message;
  }
});

document.querySelector('#freight-dimensions')?.addEventListener('input', (event) => event.currentTarget.setCustomValidity(''));

const freightDate = document.querySelector('#freight-date');
if (freightDate) {
  const preferredDate = new Date();
  preferredDate.setDate(preferredDate.getDate() + 7);
  freightDate.value = preferredDate.toISOString().slice(0, 10);
}
