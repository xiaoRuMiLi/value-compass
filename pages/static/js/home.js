(function () {
  const { normalize } = window.VC;
  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    const target = normalize(document.getElementById("q").value);
    if (target) location.href = `/report?symbol=${encodeURIComponent(target)}`;
  });

})();
