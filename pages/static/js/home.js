(function () {
  function normalize(code) {
    const value = (code || "").trim().toUpperCase();
    if (value.includes(".")) return value;
    if (/^\d{6}$/.test(value)) {
      if (value[0] === "6") return `${value}.SH`;
      if ("03".includes(value[0])) return `${value}.SZ`;
      if ("48".includes(value[0])) return `${value}.BJ`;
    }
    if (/^\d{1,5}$/.test(value)) return `${value.padStart(5, "0")}.HK`;
    if (/^[A-Z]+$/.test(value)) return `${value}.US`;
    return value;
  }

  document.getElementById("search").addEventListener("submit", (event) => {
    event.preventDefault();
    const target = normalize(document.getElementById("q").value);
    if (target) location.href = `/report?symbol=${encodeURIComponent(target)}`;
  });

})();
