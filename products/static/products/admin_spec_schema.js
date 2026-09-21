"use strict";

(function () {
  function renderHint(schema, container) {
    if (!schema || !schema.properties || Object.keys(schema.properties).length === 0) {
      container.textContent = "Brak zdefiniowanego schematu dla tej kategorii.";
      return;
    }

    const required = new Set(schema.required || []);
    const list = document.createElement("ul");
    list.style.margin = "0";
    list.style.paddingLeft = "18px";

    Object.entries(schema.properties).forEach(([key, prop]) => {
      const item = document.createElement("li");
      let text = `${key}: ${prop.type || "any"}`;
      if (prop.enum) {
        text += ` (${prop.enum.join(" | ")})`;
      }
      if (required.has(key)) {
        text += " *wymagane";
      }
      item.textContent = text;
      list.appendChild(item);
    });

    container.replaceChildren(list);
  }

  document.addEventListener("DOMContentLoaded", function () {
    const mapEl = document.getElementById("category-schema-map");
    const hintEl = document.getElementById("spec-schema-hint");
    const categorySelect = document.getElementById("id_category");
    if (!mapEl || !hintEl || !categorySelect) {
      return;
    }

    let schemaMap = {};
    try {
      schemaMap = JSON.parse(mapEl.textContent);
    } catch (err) {
      return;
    }

    function update() {
      const schema = schemaMap[categorySelect.value];
      renderHint(schema, hintEl);
    }

    categorySelect.addEventListener("change", update);
    update();
  });
})();
