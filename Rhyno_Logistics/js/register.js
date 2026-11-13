const registrationForm = document.querySelector('#register-form');
const registrationMessage = document.querySelector('#register-message');

registrationForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  registrationMessage.textContent = '';
  registrationMessage.classList.remove('error');

  const password = document.querySelector('#register-password').value;
  if (password !== document.querySelector('#register-confirm-password').value) {
    registrationMessage.textContent = 'Passwords do not match.';
    registrationMessage.classList.add('error');
    return;
  }

  const submitButton = registrationForm.querySelector('button[type="submit"]');
  submitButton.disabled = true;
  try {
    const tokenResponse = await fetch('/api/csrf-token', { credentials: 'same-origin' });
    const tokenData = await tokenResponse.json();
    if (!tokenResponse.ok) throw new Error(tokenData.error || 'Could not start registration. Refresh and try again.');

    const response = await fetch('/api/auth/register', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': tokenData.token },
      body: JSON.stringify({
        name: document.querySelector('#register-name').value,
        email: document.querySelector('#register-email').value,
        phone: document.querySelector('#register-phone').value,
        password
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Registration failed (${response.status}).`);
    window.location.assign('portal.html');
  } catch (error) {
    registrationMessage.textContent = error.message;
    registrationMessage.classList.add('error');
    submitButton.disabled = false;
  }
});
