// "Get Adam" for the shared-link pages (c/ challenges, w/ week cards). No beta links: on
// iPhone it's the App Store page once the listing is live (checked with Apple's public lookup,
// no cookies, nothing sent but the app id), "Coming soon to the App Store" until then.
(function () {
  var el = document.getElementById('get');
  if (!el) return;
  var ID = '6822980989';
  var STORE = 'https://apps.apple.com/app/id' + ID;
  if (/Android/i.test(navigator.userAgent)) {
    el.href = 'https://play.google.com/store/apps/details?id=org.camposenterprises.adam';
    el.textContent = el.getAttribute('data-get') || 'Get Adam';
    return;
  }
  var soon = function () {
    el.removeAttribute('href');
    el.className += ' soon';
    el.textContent = el.getAttribute('data-soon') || 'Adam is coming soon to the App Store';
  };
  soon();
  if (!window.fetch) return;
  fetch('https://itunes.apple.com/lookup?id=' + ID, { credentials: 'omit', referrerPolicy: 'no-referrer' })
    .then(function (r) { return r.json(); })
    .then(function (j) {
      if (!j || !j.resultCount) return;
      el.href = STORE;
      el.className = el.className.replace(' soon', '');
      el.textContent = el.getAttribute('data-get') || 'Get Adam on the App Store';
    })
    .catch(function () {});
})();
