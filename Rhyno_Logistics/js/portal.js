const requestApi = async (path, options = {}) => {
  const tokenResponse = await fetch('/api/csrf-token', { credentials: 'same-origin' });
  const tokenData = await tokenResponse.json();
  const headers = { ...(options.headers || {}), 'X-CSRF-Token': tokenData.token };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  const response = await fetch(path, { credentials: 'same-origin', ...options, headers });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
};

const escapeText = (value) => String(value ?? '').replace(/[&<>'"]/g, (character) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
}[character]));

const showMessage = (id, message, isError = false) => {
  const element = document.querySelector(id);
  element.textContent = message;
  element.classList.toggle('error', isError);
};

const portalQuery = new URLSearchParams(window.location.search);
const passwordResetToken = portalQuery.get('reset_token')
  || new URLSearchParams(window.location.hash.slice(1)).get('reset_token');
if (passwordResetToken) {
  document.querySelector('#auth-section').hidden = true;
  document.querySelector('#password-reset-confirm-panel').hidden = false;
  document.querySelector('#reset-token').value = passwordResetToken;
}

const renderBookings = (bookings) => {
  const history = document.querySelector('#booking-history');
  const summary = document.querySelector('#booking-summary');
  const categories = ['flight', 'parcel', 'clearing', 'freight'];
  summary.innerHTML = categories.map((category) => `<article><span>${category}</span><strong>${bookings.filter((booking) => booking.category === category).length}</strong></article>`).join('');
  history.dataset.bookings = JSON.stringify(bookings);
  filterBookings();
};

const filterBookings = () => {
  const history = document.querySelector('#booking-history');
  const term = document.querySelector('#booking-search').value.toLowerCase();
  const bookings = JSON.parse(history.dataset.bookings || '[]')
    .filter((booking) => `${booking.reference} ${booking.category}`.toLowerCase().includes(term));
  history.innerHTML = bookings.length ? bookings.map((booking) => {
    const payload = booking.payload || {};
    const details = payload.flight
      ? `${payload.flight.origin} → ${payload.flight.destination}; ${payload.flight.departureDate || ''}; ${payload.flight.passengers || 1} passenger(s)${payload.flight.demoMode ? ' · DEMO TEST ONLY' : ''}`
      : payload.parcel
        ? `${payload.parcel.description}; ${payload.sender?.pickupLocation || ''} → ${payload.recipient?.country || ''}`
        : payload.shipment
          ? `${payload.shipment.direction}; ${payload.shipment.origin} → ${payload.shipment.destination}`
          : `${payload.cargo?.origin || ''} → ${payload.cargo?.destination || ''}`;
    const canCancel = ['Pending', 'Pending payment', 'Pending quotation', 'Documents Received', 'Approved'].includes(booking.status);
    const flightPayable = booking.category === 'flight' && ['Pending', 'Pending payment', 'Approved'].includes(booking.status);
    const flightAmount = payload.flight?.totalAmount;
    const flightCurrency = String(payload.flight?.totalCurrency || '').toUpperCase();
    const hasApprovedQuote = booking.quote_status === 'Approved' && booking.status === 'Approved';
    const canPayPaypal = hasApprovedQuote || (flightPayable && flightAmount && flightCurrency);
    const canPayMobile = (hasApprovedQuote && String(booking.quote_currency).toUpperCase() === 'GHS') || (flightPayable && flightAmount);
    const quoteSummary = booking.quote_status === 'Approved'
      ? `<small>Approved quote: ${escapeText(booking.quote_currency)} ${escapeText(booking.quote_amount)}</small>`
      : (flightPayable && flightAmount ? `<small>Amount due: ${escapeText(flightCurrency)} ${escapeText(flightAmount)}${flightCurrency !== 'GHS' ? ' · Mobile money requires GHS' : ''}</small>` : '');
    const paypalButton = canPayPaypal
      ? `<button type="button" class="pay-booking" data-provider="paypal" data-reference="${escapeText(booking.reference)}">Pay with PayPal</button>`
      : '';
    const mobileMoneyButton = canPayMobile
      ? `<button type="button" class="pay-booking" data-provider="paystack" data-reference="${escapeText(booking.reference)}">Pay with mobile money</button>`
      : '';
    return `<article class="booking-history-item"><div><strong>${escapeText(booking.reference)}</strong><span>${escapeText(booking.category)} · ${escapeText(booking.status)}</span><small>${escapeText(details)}</small>${quoteSummary}<time>${booking.created_at ? new Date(booking.created_at).toLocaleDateString() : ''}</time></div><div class="booking-actions"><details><summary>Details</summary><pre>${escapeText(JSON.stringify(payload, null, 2))}</pre></details><a href="/api/bookings/${encodeURIComponent(booking.reference)}/receipt">Download receipt</a>${paypalButton}${mobileMoneyButton}${canCancel ? `<button type="button" class="cancel-booking" data-reference="${escapeText(booking.reference)}">Cancel request</button>` : ''}</div></article>`;
  }).join('') : '<p>No bookings found.</p>';
  history.querySelectorAll('.pay-booking').forEach((button) => button.addEventListener('click', async () => {
    button.disabled = true;
    try {
      if (button.dataset.provider === 'paystack') {
        const data = await requestApi(`/api/bookings/${encodeURIComponent(button.dataset.reference)}/paystack-mobile-money`, {
          method: 'POST', body: JSON.stringify({})
        });
        if (data.checkout_url) {
          window.location.assign(data.checkout_url);
        } else if (data.status === 'Paid') {
          await Promise.all([loadBookings(), loadNotifications()]);
          showMessage('#profile-message', data.message);
        } else {
          throw new Error('Paystack did not return a checkout link.');
        }
        return;
      }
      const data = await requestApi(`/api/bookings/${encodeURIComponent(button.dataset.reference)}/paypal-order`, {
        method: 'POST', body: JSON.stringify({})
      });
      if (data.approval_url) {
        window.location.assign(data.approval_url);
      } else if (data.already_approved) {
        const result = await requestApi('/api/payments/paypal/capture', {
          method: 'POST', body: JSON.stringify({ order_id: data.order_id })
        });
        await Promise.all([loadBookings(), loadNotifications()]);
        showMessage('#profile-message', result.message);
      }
    } catch (error) {
      showMessage('#profile-message', error.message, true);
      button.disabled = false;
    }
  }));
  history.querySelectorAll('.cancel-booking').forEach((button) => button.addEventListener('click', async () => {
    if (!window.confirm(`Cancel booking ${button.dataset.reference}?`)) return;
    try {
      await requestApi(`/api/bookings/${encodeURIComponent(button.dataset.reference)}/cancel`, { method: 'PATCH', body: JSON.stringify({}) });
      await loadBookings();
      showMessage('#profile-message', 'Booking cancelled.');
    } catch (error) { showMessage('#profile-message', error.message, true); }
  }));
};

const loadBookings = async () => {
  const data = await requestApi('/api/bookings');
  renderBookings(data.bookings);
};

const loadNotifications = async () => {
  const data = await requestApi('/api/notifications');
  const element = document.querySelector('#customer-notifications');
  element.innerHTML = data.notifications.length ? data.notifications.map((item) => `<article class="notification-item ${item.read_at ? '' : 'unread'}"><span>${escapeText(item.message)}</span><small>${item.created_at ? new Date(item.created_at).toLocaleString() : ''}</small>${item.read_at ? '' : `<button type="button" data-id="${item.id}">Mark read</button>`}</article>`).join('') : '<p>No notifications yet.</p>';
  element.querySelectorAll('button[data-id]').forEach((button) => button.addEventListener('click', async () => {
    try { await requestApi(`/api/notifications/${button.dataset.id}`, { method: 'PATCH', body: JSON.stringify({}) }); await loadNotifications(); }
    catch (error) { showMessage('#profile-message', error.message, true); }
  }));
};

async function showCustomer(customer) {
  document.querySelector('#auth-section').hidden = true;
  document.querySelector('#customer-dashboard').hidden = false;
  document.querySelector('#logout-button').hidden = false;
  document.querySelector('#welcome-message').textContent = `Welcome, ${customer.name}.`;
  document.querySelector('#profile-name').value = customer.name;
  document.querySelector('#profile-email').value = customer.email;
  document.querySelector('#profile-phone').value = customer.phone;
  await Promise.all([loadBookings(), loadNotifications()]);
}

document.querySelector('#login-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await requestApi('/api/auth/login', { method: 'POST', body: JSON.stringify({
      email: document.querySelector('#login-email').value, password: document.querySelector('#login-password').value
    }) });
    await showCustomer(data.customer);
  } catch (error) { showMessage('#login-message', error.message, true); }
});

document.querySelector('#show-password-reset').addEventListener('click', () => {
  const panel = document.querySelector('#password-reset-request-panel');
  panel.hidden = !panel.hidden;
});

document.querySelector('#password-reset-request-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await requestApi('/api/auth/password-reset/request', {
      method: 'POST',
      body: JSON.stringify({ email: document.querySelector('#reset-request-email').value.trim() })
    });
    showMessage('#reset-request-message', data.message);
  } catch (error) {
    showMessage('#reset-request-message', error.message, true);
  }
});

document.querySelector('#password-reset-confirm-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const newPassword = document.querySelector('#reset-new-password').value;
  if (newPassword !== document.querySelector('#reset-confirm-password').value) {
    return showMessage('#reset-confirm-message', 'Passwords do not match.', true);
  }
  try {
    const data = await requestApi('/api/auth/password-reset/confirm', {
      method: 'POST',
      body: JSON.stringify({ token: document.querySelector('#reset-token').value, new_password: newPassword })
    });
    window.history.replaceState({}, '', window.location.pathname);
    document.querySelector('#password-reset-confirm-panel').hidden = true;
    document.querySelector('#auth-section').hidden = false;
    showMessage('#login-message', data.message);
  } catch (error) {
    showMessage('#reset-confirm-message', error.message, true);
  }
});

document.querySelector('#profile-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await requestApi('/api/profile', { method: 'PATCH', body: JSON.stringify({
      name: document.querySelector('#profile-name').value, email: document.querySelector('#profile-email').value,
      phone: document.querySelector('#profile-phone').value
    }) });
    document.querySelector('#welcome-message').textContent = `Welcome, ${data.customer.name}.`;
    showMessage('#profile-message', data.message);
  } catch (error) { showMessage('#profile-message', error.message, true); }
});

document.querySelector('#password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  try {
    const data = await requestApi('/api/profile/password', { method: 'POST', body: JSON.stringify({
      current_password: document.querySelector('#current-password').value,
      new_password: document.querySelector('#new-password').value
    }) });
    event.currentTarget.reset();
    showMessage('#password-message', data.message);
  } catch (error) { showMessage('#password-message', error.message, true); }
});

document.querySelector('#refresh-bookings').addEventListener('click', async () => {
  try { await loadBookings(); } catch (error) { showMessage('#profile-message', error.message, true); }
});
document.querySelector('#refresh-notifications').addEventListener('click', async () => {
  try { await loadNotifications(); } catch (error) { showMessage('#profile-message', error.message, true); }
});
document.querySelector('#booking-search').addEventListener('input', filterBookings);
document.querySelector('#logout-button').addEventListener('click', async () => {
  try { await requestApi('/api/auth/logout', { method: 'POST' }); window.location.reload(); }
  catch (error) { showMessage('#profile-message', error.message, true); }
});

const paypalOrderId = portalQuery.get('token');
const paypalRedirect = portalQuery.get('paypal');
const paystackReference = portalQuery.get('reference');
const paystackRedirect = portalQuery.get('paystack');
let profileReady = Promise.resolve();
if (!passwordResetToken) {
  profileReady = requestApi('/api/auth/status').then(({ authenticated }) => {
    if (!authenticated) return;
    return requestApi('/api/profile').then(({ customer }) => showCustomer(customer));
  }).catch((error) => {
    showMessage('#login-message', error.message, true);
  });
}
const showPortalMessage = (message, isError = false) => {
  const signedIn = !document.querySelector('#customer-dashboard').hidden;
  showMessage(signedIn ? '#profile-message' : '#login-message', message, isError);
};
if (paypalOrderId && paypalRedirect === 'return') {
  profileReady.then(() => requestApi('/api/payments/paypal/capture', {
    method: 'POST', body: JSON.stringify({ order_id: paypalOrderId })
  })).then(async (data) => {
    window.history.replaceState({}, '', window.location.pathname);
    await Promise.all([loadBookings(), loadNotifications()]);
    showPortalMessage(data.message);
  }).catch((error) => showPortalMessage(error.message, true));
} else if (paypalOrderId && paypalRedirect === 'cancel') {
  profileReady.then(() => requestApi('/api/payments/paypal/cancel', {
    method: 'POST', body: JSON.stringify({ order_id: paypalOrderId })
  })).then((data) => {
    window.history.replaceState({}, '', window.location.pathname);
    showPortalMessage(data.message);
  }).catch((error) => showPortalMessage(error.message, true));
}
if (paystackReference && paystackRedirect === 'return') {
  profileReady.then(() => requestApi('/api/payments/paystack/verify', {
    method: 'POST', body: JSON.stringify({ reference: paystackReference })
  })).then(async (data) => {
    window.history.replaceState({}, '', window.location.pathname);
    await Promise.all([loadBookings(), loadNotifications()]);
    showPortalMessage(data.message);
  }).catch((error) => showPortalMessage(error.message, true));
}
