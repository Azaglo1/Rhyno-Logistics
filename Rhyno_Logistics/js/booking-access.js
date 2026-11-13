const accountRequiredForms = [...document.querySelectorAll('#passenger-form, #parcel-form, #clearing-form, #freight-form')];
let bookingAccessNotice = document.querySelector('#booking-access-notice');
let bookingAccessMessage = document.querySelector('#booking-access-message');
let customerSignedIn = false;

if (accountRequiredForms.length) {
  const submitButtons = accountRequiredForms
    .map((form) => form.querySelector('button[type="submit"]'))
    .filter(Boolean);
  submitButtons.forEach((button) => { button.disabled = true; });

  if (!bookingAccessNotice) {
    bookingAccessNotice = document.createElement('div');
    bookingAccessNotice.className = 'booking-access-notice';
    bookingAccessNotice.hidden = true;
    bookingAccessNotice.setAttribute('role', 'status');
    bookingAccessMessage = document.createElement('p');
    bookingAccessMessage.id = 'booking-access-message';
    const accountLink = document.createElement('a');
    accountLink.href = 'portal.html#auth-section';
    accountLink.textContent = 'Create account or sign in';
    bookingAccessNotice.append(bookingAccessMessage, accountLink);
    const anchor = document.querySelector('.freight-booking .section-heading') || accountRequiredForms[0];
    anchor.before(bookingAccessNotice);
  }

  const showBookingAccessNotice = (message) => {
    if (bookingAccessMessage) bookingAccessMessage.textContent = message;
    if (bookingAccessNotice) bookingAccessNotice.hidden = false;
  };

  accountRequiredForms.forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (!customerSignedIn) {
        event.preventDefault();
        event.stopImmediatePropagation();
        showBookingAccessNotice('Create an account or sign in before submitting a booking request.');
      }
    }, true);
  });

  fetch('/api/auth/status', { credentials: 'same-origin' })
    .then(async (response) => {
      if (!response.ok) throw new Error(`Account verification failed (${response.status}).`);
      const data = await response.json();
      if (!data.authenticated) {
        showBookingAccessNotice('Create an account or sign in before submitting a booking request. You can continue browsing and searching flights.');
        return;
      }
      customerSignedIn = true;
      submitButtons.forEach((button) => { button.disabled = false; });
      if (bookingAccessNotice) bookingAccessNotice.hidden = true;
    })
    .catch((error) => {
      console.error('Unable to verify customer account before booking.', error);
      showBookingAccessNotice('We could not verify your account. Reload this page or sign in again before booking.');
    });
}
