/* WizzOS service worker – bara för "Dela till WizzOS" från mobilens dela-meny.
   Allt annat går rakt till servern som vanligt (ingen cache, ingen offline-magi). */
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", (e) => e.waitUntil(self.clients.claim()));

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "POST" || url.pathname !== "/share-in") return;
  e.respondWith((async () => {
    try {
      const form = await e.request.formData();
      const cache = await caches.open("wz-share");
      for (const k of await cache.keys()) await cache.delete(k);  // bara den senaste delningen
      let i = 0;
      for (const f of form.getAll("files")) {
        if (!(f instanceof File)) continue;
        await cache.put(`/share-pending/${i++}`, new Response(f, { headers: {
          "Content-Type": f.type || "application/octet-stream",
          "X-Name": encodeURIComponent(f.name || `delad-${Date.now()}`), "X-Size": String(f.size) } }));
      }
      const text = [form.get("title"), form.get("text"), form.get("url")].filter(Boolean).join("\n");
      if (text) await cache.put("/share-pending/text", new Response(text));
    } catch (err) { /* sidan visar att inget kom fram */ }
    return Response.redirect("/#/dela", 303);
  })());
});
