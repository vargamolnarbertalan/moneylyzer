// Applies the saved theme before first paint (an inline script would violate the CSP).
(function () {
  try {
    var t = window.localStorage.getItem("lia-theme");
    document.documentElement.dataset.theme = t === "light" ? "light" : "dark";
  } catch (e) {
    document.documentElement.dataset.theme = "dark";
  }
})();
