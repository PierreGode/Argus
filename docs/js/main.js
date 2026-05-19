if (!("serial" in navigator)) {
  document.getElementById("browserWarn").style.display = "block";
}

fetch("version.json", { cache: "no-store" })
  .then((r) => (r.ok ? r.json() : null))
  .then((v) => {
    if (v && v.build) document.getElementById("build").textContent = v.build;
  })
  .catch(() => {});

// ---- Splash sprite cycler (mirrors firmware src/splash.cpp) -------
// The mascot has six expressions packed as PNGs in /img. We cross-
// fade between them every few seconds; the JS only needs to flip
// which image is marked .active.
(function runSpriteCycler() {
  const sprites = [
    document.getElementById("sprite-happy"),
    document.getElementById("sprite-looking"),
    document.getElementById("sprite-flirt"),
    document.getElementById("sprite-buffeld"),
    document.getElementById("sprite-surprised"),
    document.getElementById("sprite-angry"),
  ].filter(Boolean);
  if (sprites.length === 0) return;

  let i = 0;
  function tick() {
    sprites.forEach((s, idx) => s.classList.toggle("active", idx === i));
    i = (i + 1) % sprites.length;
    // Slightly randomised interval so two open tabs don't pulse in lockstep.
    const next = 3500 + Math.random() * 1500;
    setTimeout(tick, next);
  }
  // First flip after a beat so the initial render is visible.
  setTimeout(tick, 2500);
})();
