const currentPage = document.body.dataset.page;
const navigationPage = ['flights', 'parcels', 'clearing', 'freight'].includes(currentPage)
  ? 'services'
  : currentPage === 'register' ? 'portal' : currentPage;
const siteHeader = `
  <header class="site-header" id="top">
    <div class="container header-inner">
      <a class="brand" href="index.html" aria-label="Rhyno Logistics home">
        <img src="assets/images/logo.svg" alt="Rhyno Logistics" />
        <span><strong>RHYNO</strong><small>LOGISTICS</small></span>
      </a>
      <button class="menu-toggle" type="button" aria-expanded="false" aria-controls="main-navigation">
        <span></span><span></span><span></span><b>Menu</b>
      </button>
      <nav class="main-nav" id="main-navigation" aria-label="Primary navigation">
        <a class="nav-link" data-page="home" href="index.html">Home</a>
        <a class="nav-link" data-page="about" href="about.html">About</a>
        <a class="nav-link" data-page="services" href="services.html">Services</a>
        <a class="nav-link" data-page="portal" href="portal.html">Portal</a>
        <a class="nav-link" data-page="about" href="about.html#how-it-works">How It Works</a>
        <a class="nav-link" data-page="contact" href="contact.html">Contact</a>
      </nav>
      <div class="nav-actions">
        ${currentPage === 'portal' ? '<button id="logout-button" class="primary-button compact" type="button" hidden>Log out</button>' : ''}
        <a class="text-button profile-link" href="portal.html#profile-section">Profile</a>
        <a class="primary-button compact" href="services.html">Book Now <span aria-hidden="true">→</span></a>
      </div>
    </div>
  </header>`;

const siteFooter = `
  <footer class="site-footer">
    <div class="container footer-grid">
      <div>
        <a class="brand footer-brand" href="index.html">
          <img src="assets/images/logo.svg" alt="Rhyno Logistics" />
          <span><strong>RHYNO</strong><small>LOGISTICS</small></span>
        </a>
        <p>Moving people, parcels, and possibilities with confidence.</p>
      </div>
      <div><h3>Navigate</h3><a href="about.html">About us</a><a href="services.html">Services</a><a href="about.html#why-us">Why Rhyno</a><a href="about.html#faq">FAQs</a></div>
      <div><h3>Services</h3><a href="flights.html">Air tickets</a><a href="parcels.html">Parcel delivery</a><a href="clearing.html">Customs clearing</a><a href="freight.html">Cargo &amp; freight</a></div>
      <div><h3>Get in touch</h3><p>Questions about your next move? Our team is ready to help.</p><a href="contact.html">Contact Rhyno Logistics →</a></div>
    </div>
    <div class="container footer-bottom">
      <span>© 2026 Rhyno Logistics. All rights reserved.</span>
      <span><a href="contact.html">Privacy</a><a href="contact.html">Terms</a></span>
    </div>
  </footer>`;

const headerSlot = document.querySelector('#site-header');
const footerSlot = document.querySelector('#site-footer');
if (headerSlot) headerSlot.outerHTML = siteHeader;
if (footerSlot) footerSlot.outerHTML = siteFooter;

document.querySelectorAll('.nav-link[data-page]').forEach((link) => {
  if (link.dataset.page === navigationPage && !link.hash) {
    link.classList.add('active');
    link.setAttribute('aria-current', 'page');
  }
});

const menuToggle = document.querySelector('.menu-toggle');
const mainNav = document.querySelector('.main-nav');
menuToggle?.addEventListener('click', () => {
  const isOpen = mainNav.classList.toggle('open');
  menuToggle.setAttribute('aria-expanded', String(isOpen));
});

document.querySelectorAll('.nav-link').forEach((link) => {
  link.addEventListener('click', () => {
    mainNav.classList.remove('open');
    menuToggle?.setAttribute('aria-expanded', 'false');
  });
});
