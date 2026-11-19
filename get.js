// "Get Adam" for the shared-link pages (c/ challenges, w/ week cards). Adam is live on both
// stores (App Store approved 2026-11-18), so this only picks the right store: Google Play on
// Android, the App Store everywhere else. No network requests.
(function () {
  var el = document.getElementById('get');
  if (!el) return;
  if (/Android/i.test(navigator.userAgent)) {
    el.href = 'https://play.google.com/store/apps/details?id=org.camposenterprises.adam';
    el.textContent = el.getAttribute('data-get') ? el.getAttribute('data-get').replace(/(the |el )?App Store/, 'Google Play') : 'Get Adam on Google Play';
    return;
  }
  el.href = 'https://apps.apple.com/app/id6822980989';
  el.textContent = el.getAttribute('data-get') || 'Get Adam on the App Store';
})();
