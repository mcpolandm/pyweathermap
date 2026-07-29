// pyweathermap index overlay — building/rack/shelf cascading filters.
// HOW TO USE:
// 1. Copy into repo/pyweathermap/static/
// 2. Jump to USER EDITS HERE
// 3. Update the matching conditions for building, rack, and shelf to allign with expected names.
// 4. Inject into index.html via <script src="/static/filters.js"> at end of <body>.
(function () {
  'use strict';

  const STYLE = `
    .layout {
      /* Sized only by .card — .filter-card is positioned absolutely so its
         height changes never affect this box, which is what body centers on. */
      position: relative;
    }
    .filter-card {
      position: absolute;
      top: 0;
      right: 100%;
      margin-right: 24px;
      background: #16213e;
      border: 1px solid #0f3460;
      border-radius: 8px;
      padding: 20px 24px;
      box-shadow: 0 4px 24px rgba(0,0,0,0.5);
      display: flex;
      flex-direction: column;
      gap: 18px;
      min-width: 160px;
    }
    .filter-section h2 {
      font-size: 0.8rem;
      color: #e94560;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      text-align: left;
      margin-bottom: 8px;
    }
    .filter-rows {
      display: flex;
      flex-direction: column;
      gap: 4px;
      max-height: 160px;
      overflow-y: auto;
    }
    .filter-row {
      display: flex;
      align-items: center;
      gap: 8px;
    }
    .filter-toggle {
      width: 18px;
      height: 18px;
      border-radius: 4px;
      border: 1px solid #0f3460;
      background: #1a1a2e;
      cursor: pointer;
      padding: 0;
      flex-shrink: 0;
    }
    .filter-toggle.active {
      background: #e94560;
      border-color: #e94560;
    }
    .filter-row span {
      font-size: 0.9rem;
      text-align: left;
    }
  `;
  // Removes leading zeros.
  function normalizeNumeric(str) {
    return str.replace(/^0+(?=\d)/, '');
  }

  // Parses filtering information from a switch name.
  function parseSwitchName(name) {
    // USER EDITS HERE:
    // Include matching schemes for building name, rack number, and shelf number.
    // This allows the landing page to filter the dropdown accordingly.
    const upper = name.toUpperCase();
    const building = upper.includes(null) ? ''
                   : null;
    const rackMatch = upper.match(null);
    const shelfMatch = upper.match(null);
    return {
      building,
      rack: rackMatch ? normalizeNumeric(rackMatch[1]) : null,
      shelf: shelfMatch ? normalizeNumeric(shelfMatch[1]) : null,
    };
  }

  // Returns all entries that match the given filters.
  function matchesFilters(facets, filters) {
    return Object.entries(filters).every(([key, selected]) =>
      selected.length === 0 || (facets[key] !== null && selected.includes(facets[key]))
    );
  }

  // Dedupes and sorts switches.
  function uniqueSorted(values) {
    return [...new Set(values.filter(v => v !== null))]
      .sort((a, b) => a.localeCompare(b, undefined, { numeric: true, sensitivity: 'base' }));
  }

  // Creates the HTML elements for the filters
  function buildFilterSection(labelText) {
    const section = document.createElement('div');
    section.className = 'filter-section';

    const heading = document.createElement('h2');
    heading.textContent = labelText;

    const rows = document.createElement('div');
    rows.className = 'filter-rows';

    section.appendChild(heading);
    section.appendChild(rows);

    return { section, rows, selected: new Set() };
  }

  // Rebuilds a facet's rows from a value list, preserving selections that
  // are still valid, and wires each row's toggle button to call onToggle.
  function rebuildFacetRows(facet, values, onToggle) {
    const prevSelected = new Set(facet.selected);
    facet.rows.innerHTML = '';
    facet.selected.clear();

    values.forEach(value => {
      const row = document.createElement('div');
      row.className = 'filter-row';

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'filter-toggle';

      if (prevSelected.has(value)) {
        btn.classList.add('active');
        facet.selected.add(value);
      }
      btn.addEventListener('click', () => {
        if (facet.selected.has(value)) {
          facet.selected.delete(value);
          btn.classList.remove('active');
        } else {
          facet.selected.add(value);
          btn.classList.add('active');
        }
        onToggle();
      });

      const label = document.createElement('span');
      label.textContent = value;

      row.appendChild(btn);
      row.appendChild(label);
      facet.rows.appendChild(row);
    });
  }

  // Builds filters on load
  document.addEventListener('DOMContentLoaded', () => {
    const switchSelect = document.getElementById('switch-select');
    const card = document.querySelector('.card');
    if (!switchSelect || !card) return;

    const styleEl = document.createElement('style');
    styleEl.textContent = STYLE;
    document.head.appendChild(styleEl);

    // Parse filters from switch names, then build filter sections.
    const options = Array.from(switchSelect.options);
    options.forEach(opt => {
      opt._facets = parseSwitchName(opt.dataset.name || opt.textContent);
    });

    const buildingFacet = buildFilterSection('Building');
    const rackFacet = buildFilterSection('Rack');
    const shelfFacet = buildFilterSection('Shelf');

    const filterCard = document.createElement('div');
    filterCard.className = 'filter-card';
    filterCard.appendChild(buildingFacet.section);
    filterCard.appendChild(rackFacet.section);
    filterCard.appendChild(shelfFacet.section);

    // Wrap the existing .card in a new .layout row and place the filter
    // card to its left, without touching index.html's own markup.
    const layout = document.createElement('div');
    layout.className = 'layout';
    card.parentNode.insertBefore(layout, card);
    layout.appendChild(filterCard);
    layout.appendChild(card);

    // Hides/displays correct switches from filters.
    function applySwitchFilters() {
      const filters = {
        building: [...buildingFacet.selected],
        rack: [...rackFacet.selected],
        shelf: [...shelfFacet.selected],
      };
      options.forEach(opt => {
        opt.hidden = !matchesFilters(opt._facets, filters);
      });
    }

    // Updates which Shelf filter options are displayed.
    function refreshShelf() {
      // Rack/shelf lists only make sense once a building narrows the set down;
      // with no building selected, don't dump every rack/shelf in the org.
      if (buildingFacet.selected.size === 0) {
        rebuildFacetRows(shelfFacet, [], applySwitchFilters);
        applySwitchFilters();
        return;
      }
      const candidates = options.filter(opt => matchesFilters(opt._facets, {
        building: [...buildingFacet.selected],
        rack: [...rackFacet.selected],
      }));
      rebuildFacetRows(shelfFacet, uniqueSorted(candidates.map(o => o._facets.shelf)), applySwitchFilters);
      applySwitchFilters();
    }

    // Updates which Rack filter options are displayed.
    function refreshRack() {
      if (buildingFacet.selected.size === 0) {
        rebuildFacetRows(rackFacet, [], refreshShelf);
        refreshShelf();
        return;
      }
      const candidates = options.filter(opt => matchesFilters(opt._facets, {
        building: [...buildingFacet.selected],
        }));
      rebuildFacetRows(rackFacet, uniqueSorted(candidates.map(o => o._facets.rack)), refreshShelf);
      refreshShelf();
    }

    // Displays initial filters (by building only)
    rebuildFacetRows(buildingFacet, uniqueSorted(options.map(o => o._facets.building)), refreshRack);
    refreshRack();
  });
})();
