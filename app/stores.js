// Store buttons on the Adam landing page. Both stores are live; the App Store button comes first,
// except on Android, where Google Play is swapped into the first (solid) button. No network requests.
(function () {
  if (!/Android/i.test(navigator.userAgent)) return;
  var firsts = document.querySelectorAll('[data-first]');
  for (var i = 0; i < firsts.length; i++) {
    var a = firsts[i], b = a.parentNode.querySelector('[data-second]');
    if (!b) continue;
    var h = a.href, t = a.textContent;
    a.href = b.href; a.textContent = b.textContent; b.href = h; b.textContent = t;
  }
})();
