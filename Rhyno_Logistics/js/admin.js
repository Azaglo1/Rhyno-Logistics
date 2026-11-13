const adminApi = async (path, options = {}) => {
  const csrfResponse = await fetch('/api/csrf-token', { credentials: 'same-origin' });
  const csrf = await csrfResponse.json();
  const headers = { ...(options.headers || {}), 'X-CSRF-Token': csrf.token };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
};

const escapeText = (value) => String(value ?? '').replace(/[&<>'"]/g, (char) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[char]));
const showAdminMessage = (message, error = false) => {
  const element = document.querySelector('#admin-message');
  element.textContent = message;
  element.classList.toggle('error', error);
};

document.querySelectorAll('.admin-tabs button').forEach((button) => button.addEventListener('click', () => {
  document.querySelectorAll('.admin-tabs button').forEach((tab) => tab.classList.toggle('active', tab === button));
  document.querySelectorAll('.admin-panel').forEach((panel) => panel.classList.toggle('active', panel.id === `panel-${button.dataset.tab}`));
}));

async function loadAdminData() {
  try {
    const [overview, bookings, customers, shipments, quotations, payments] = await Promise.all([
      adminApi('/api/admin/overview'), adminApi('/api/admin/bookings'), adminApi('/api/admin/customers'),
      adminApi('/api/admin/shipments'), adminApi('/api/admin/quotations'), adminApi('/api/admin/payments')
    ]);
    document.querySelector('#admin-overview').innerHTML = Object.entries(overview.counts)
      .map(([label, count]) => `<article><span>${escapeText(label.replaceAll('_', ' '))}</span><strong>${count}</strong></article>`).join('');
    renderBookings(bookings.bookings);
    renderCustomers(customers.customers);
    renderShipments(shipments.shipments);
    renderQuotations(quotations.quotations);
    renderPayments(payments.payments);
    showAdminMessage('Dashboard data refreshed.');
  } catch (error) {
    showAdminMessage(error.message, true);
    if (error.message.includes('Administrator sign-in required')) signOut();
  }
}

function renderBookings(bookings) {
  const filter = document.querySelector('#booking-category-filter').value;
  const statuses = ['Pending', 'Pending quotation', 'Documents Received', 'Under Review', 'Approved', 'Rejected', 'Processing', 'Cleared', 'Completed', 'Cancelled'];
  document.querySelector('#admin-bookings').innerHTML = bookings.filter((item) => !filter || item.category === filter).map((booking) => {
    const data = booking.payload || {};
    const contact = data.customer || data.sender || {};
    const description = data.flight
      ? `${data.flight.origin} → ${data.flight.destination}; ${data.flight.departureDate || ''}; ${data.flight.passengers || 1} passenger(s)${data.flight.demoMode ? ' · DEMO TEST ONLY' : ''}`
      : data.parcel
        ? `${data.parcel.description}; ${data.sender?.pickupLocation || ''} → ${data.recipient?.country || ''}`
        : data.shipment
          ? `${data.shipment.direction}; ${data.shipment.origin} → ${data.shipment.destination}`
          : data.cargo
            ? `${data.cargo.type}; ${data.cargo.origin} → ${data.cargo.destination}`
            : '';
    const docs = (data.documents || []).map((document) => `<a href="/api/admin/documents/${encodeURIComponent(booking.reference)}/${encodeURIComponent(document.storage_name)}">${escapeText(document.field)}: ${escapeText(document.filename)}</a>`).join('');
    return `<tr><td><strong>${escapeText(booking.reference)}</strong><details><summary>Details</summary><div class="booking-detail">${escapeText(JSON.stringify({ contact, request: data }, null, 2))}</div></details></td><td>${escapeText(booking.category)}</td><td>${escapeText(description)}</td><td>${escapeText(booking.status)}</td><td>${docs || '—'}</td><td><select aria-label="Status for ${escapeText(booking.reference)}">${statuses.map((status) => `<option ${status === booking.status ? 'selected' : ''}>${status}</option>`).join('')}</select><button type="button" data-reference="${escapeText(booking.reference)}">Save status</button></td></tr>`;
  }).join('') || '<tr><td colspan="6">No bookings found.</td></tr>';
  document.querySelectorAll('#admin-bookings button[data-reference]').forEach((button) => button.addEventListener('click', async () => {
    const status = button.parentElement.querySelector('select').value;
    try {
      await adminApi(`/api/admin/bookings/${encodeURIComponent(button.dataset.reference)}`, { method: 'PATCH', body: JSON.stringify({ status }) });
      showAdminMessage(`Booking ${button.dataset.reference} updated to ${status}.`);
      await loadAdminData();
    } catch (error) { showAdminMessage(error.message, true); }
  }));
}

function renderCustomers(customers) {
  document.querySelector('#admin-customers').innerHTML = customers.map((customer) => `<tr data-customer-id="${customer.id}"><td><input data-field="name" aria-label="Name" value="${escapeText(customer.name)}" required /></td><td><input data-field="email" aria-label="Email" type="email" value="${escapeText(customer.email)}" required /></td><td><input data-field="phone" aria-label="Phone" value="${escapeText(customer.phone)}" required /></td><td>${customer.created_at ? new Date(customer.created_at).toLocaleDateString() : ''}</td><td>${customer.active ? 'Active' : 'Disabled'}</td><td><select data-field="active" aria-label="Account status"><option value="1" ${customer.active ? 'selected' : ''}>Active</option><option value="0" ${customer.active ? '' : 'selected'}>Disabled</option></select><button type="button" data-customer-id="${customer.id}">Save</button></td></tr>`).join('') || '<tr><td colspan="6">No customer accounts found.</td></tr>';
  document.querySelectorAll('#admin-customers button[data-customer-id]').forEach((button) => button.addEventListener('click', async () => {
    const row = button.closest('tr');
    const values = Object.fromEntries([...row.querySelectorAll('[data-field]')].map((input) => [input.dataset.field, input.value]));
    try {
      await adminApi(`/api/admin/customers/${encodeURIComponent(button.dataset.customerId)}`, { method: 'PATCH', body: JSON.stringify(values) });
      showAdminMessage(`Customer account ${button.dataset.customerId} updated.`);
      await loadAdminData();
    } catch (error) { showAdminMessage(error.message, true); }
  }));
}

function renderShipments(shipments) {
  document.querySelector('#admin-shipments').innerHTML = shipments.map((shipment) => `<tr><td><strong>${escapeText(shipment.tracking_number)}</strong></td><td>${escapeText(shipment.booking_reference || '')}</td><td>${escapeText(shipment.origin)} → ${escapeText(shipment.destination)}</td><td>${escapeText(shipment.status)}<br />${escapeText(shipment.location || '')}</td><td><form class="shipment-update" data-tracking="${escapeText(shipment.tracking_number)}"><input name="status" aria-label="Status" value="${escapeText(shipment.status)}" required /><input name="location" aria-label="Current location" value="${escapeText(shipment.location || '')}" required /><input name="eta" aria-label="Estimated delivery" type="datetime-local" value="${shipment.eta ? new Date(shipment.eta).toISOString().slice(0, 16) : ''}" /><button type="submit">Update</button></form></td></tr>`).join('') || '<tr><td colspan="5">No shipment records found.</td></tr>';
  document.querySelectorAll('.shipment-update').forEach((form) => form.addEventListener('submit', async (event) => {
    event.preventDefault();
    const values = Object.fromEntries(new FormData(form));
    try {
      await adminApi(`/api/admin/shipments/${encodeURIComponent(form.dataset.tracking)}`, { method: 'PATCH', body: JSON.stringify(values) });
      showAdminMessage(`Tracking record ${form.dataset.tracking} updated.`);
      await loadAdminData();
    } catch (error) { showAdminMessage(error.message, true); }
  }));
}

function renderQuotations(quotations) {
  document.querySelector('#admin-quotations').innerHTML = quotations.map((quote) => `<tr><td>${escapeText(quote.reference)}</td><td>${escapeText(quote.category)}</td><td>${escapeText(quote.status)}</td><td>${quote.amount == null ? '—' : `${escapeText(quote.currency)} ${Number(quote.amount).toFixed(2)}`}</td><td>${escapeText(quote.notes || '')}</td></tr>`).join('') || '<tr><td colspan="5">No quotations found.</td></tr>';
}

function renderPayments(payments) {
  document.querySelector('#admin-payments').innerHTML = payments.map((payment) => `<tr><td>${escapeText(payment.reference)}</td><td>${escapeText(payment.category)}</td><td>${escapeText(payment.currency || '')} ${escapeText(payment.amount)}</td><td>${escapeText(payment.status)}</td><td>${escapeText(payment.provider)}</td><td>${escapeText(payment.provider_reference || '')}</td><td>${payment.created_at ? new Date(payment.created_at).toLocaleString() : ''}</td></tr>`).join('') || '<tr><td colspan="7">No payment records found.</td></tr>';
}

function signOut() {
  document.querySelector('#admin-app').hidden = true;
  document.querySelector('#admin-auth').hidden = false;
  document.querySelector('#admin-logout').hidden = true;
}

document.querySelector('#admin-login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const message = document.querySelector('#admin-auth-message');
  message.textContent = 'Signing in…';
  try {
    await adminApi('/api/admin/login', { method: 'POST', body: JSON.stringify({
      email: document.querySelector('#admin-email').value, password: document.querySelector('#admin-password').value
    }) });
    document.querySelector('#admin-auth').hidden = true;
    document.querySelector('#admin-app').hidden = false;
    document.querySelector('#admin-logout').hidden = false;
    await loadAdminData();
  } catch (error) { message.textContent = error.message; }
});

document.querySelector('#admin-logout').addEventListener('click', async () => {
  try { await adminApi('/api/auth/logout', { method: 'POST' }); signOut(); }
  catch (error) { showAdminMessage(error.message, true); }
});
document.querySelector('#refresh-dashboard').addEventListener('click', loadAdminData);
document.querySelector('#booking-category-filter').addEventListener('change', () => loadAdminData());

document.querySelector('#shipment-create-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const values = Object.fromEntries(new FormData(form));
  if (values.eta) values.eta = new Date(values.eta).toISOString();
  try {
    const data = await adminApi('/api/admin/shipments', { method: 'POST', body: JSON.stringify(values) });
    showAdminMessage(`Shipment created. Tracking number: ${data.tracking_number}`);
    form.reset();
    await loadAdminData();
  } catch (error) { showAdminMessage(error.message, true); }
});

document.querySelector('#quotation-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.currentTarget));
  try {
    await adminApi('/api/admin/quotations', { method: 'POST', body: JSON.stringify(values) });
    showAdminMessage('Quotation saved.');
    await loadAdminData();
  } catch (error) { showAdminMessage(error.message, true); }
});

document.querySelector('#admin-app').hidden = true;
adminApi('/api/admin/overview').then(() => {
  document.querySelector('#admin-auth').hidden = true;
  document.querySelector('#admin-app').hidden = false;
  document.querySelector('#admin-logout').hidden = false;
  return loadAdminData();
}).catch(() => {});
