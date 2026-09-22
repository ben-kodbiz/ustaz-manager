(function () {
  var input = document.getElementById("q");
  var results = document.getElementById("results");
  if (!input || !results) return;

  var data = [];

  function rootPrefix() {
    // pages live at /search/ under site root
    return "/";
  }

  function render(items) {
    if (!items.length) {
      results.innerHTML = '<p class="empty">No matches.</p>';
      return;
    }
    var html = "";
    for (var i = 0; i < items.length; i++) {
      var v = items[i];
      var href = rootPrefix() + "video/" + encodeURIComponent(v.id) + "/";
      var title = (v.title || v.id)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      var tag = v.category
        ? '<span class="tag">' + String(v.category).replace(/</g, "&lt;") + "</span>"
        : "";
      html +=
        '<article class="card"><h3><a href="' +
        href +
        '">' +
        title +
        "</a></h3><p class=\"meta\">" +
        tag +
        "</p></article>";
    }
    results.innerHTML = html;
  }

  function search(q) {
    q = (q || "").trim().toLowerCase();
    if (!q) {
      results.innerHTML = "";
      return;
    }
    var hits = [];
    for (var i = 0; i < data.length; i++) {
      var v = data[i];
      var hay = (
        (v.title || "") +
        " " +
        (v.description || "") +
        " " +
        (v.category || "") +
        " " +
        (v.series || "") +
        " " +
        (v.playlist || "")
      ).toLowerCase();
      if (hay.indexOf(q) !== -1) hits.push(v);
      if (hits.length >= 50) break;
    }
    render(hits);
  }

  input.addEventListener("input", function () {
    search(input.value);
  });

  fetch(rootPrefix() + "data/search.json")
    .then(function (r) {
      return r.json();
    })
    .then(function (json) {
      data = json || [];
      var params = new URLSearchParams(window.location.search);
      var q = params.get("q");
      if (q) {
        input.value = q;
        search(q);
      }
    })
    .catch(function () {
      results.innerHTML = '<p class="empty">Search data unavailable.</p>';
    });
})();
