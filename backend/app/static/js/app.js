/* page helpers: theme, hints, live run log */
(function () {
  var root = document.documentElement;
  try {
    var saved = localStorage.getItem("yandu-theme");
    if (saved) root.setAttribute("data-theme", saved);
  } catch (e) {}
  var btn = document.getElementById("theme-toggle");
  if (btn) {
    btn.addEventListener("click", function () {
      var next = root.getAttribute("data-theme") === "night" ? "day" : "night";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("yandu-theme", next); } catch (e) {}
    });
  }

  function closeHints(except) {
    document.querySelectorAll(".hint-wrap.is-open").forEach(function (el) {
      if (el !== except) {
        el.classList.remove("is-open");
        var b = el.querySelector(".hint");
        if (b) b.setAttribute("aria-expanded", "false");
      }
    });
  }
  document.addEventListener("click", function (e) {
    var wrap = e.target.closest(".hint-wrap");
    var btnHint = e.target.closest(".hint");
    if (btnHint && wrap) {
      var open = wrap.classList.toggle("is-open");
      btnHint.setAttribute("aria-expanded", open ? "true" : "false");
      closeHints(wrap);
      e.preventDefault();
      e.stopPropagation();
      return;
    }
    if (!wrap) closeHints();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape") closeHints();
  });
})();
