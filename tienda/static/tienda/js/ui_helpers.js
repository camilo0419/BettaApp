document.addEventListener("DOMContentLoaded", function () {
  function optionMatchesSearch(option, query) {
    return !query || option.textContent.toLowerCase().includes(query);
  }

  function optionMatchesDependencies(select, option) {
    if (!option.value) {
      return true;
    }

    const clientSourceId = select.getAttribute("data-filter-client-source");
    const projectSourceId = select.getAttribute("data-filter-project-source");

    if (clientSourceId) {
      const source = document.getElementById(clientSourceId);
      const selectedClient = source ? source.value : "";
      const optionClient = option.getAttribute("data-client-id") || "";

      if (selectedClient && optionClient && optionClient !== selectedClient) {
        return false;
      }
    }

    if (projectSourceId) {
      const source = document.getElementById(projectSourceId);
      const selectedProject = source ? source.value : "";
      const optionProject = option.getAttribute("data-project-id") || "";

      if (selectedProject && optionProject && optionProject !== selectedProject) {
        return false;
      }
    }

    return true;
  }

  function refreshSelect(select, input) {
    const query = (input ? input.value : "").trim().toLowerCase();
    let selectedStillVisible = !select.value;

    Array.from(select.options).forEach(function (option) {
      const visible = optionMatchesSearch(option, query) && optionMatchesDependencies(select, option);
      option.hidden = !visible;
      option.disabled = !visible && !!option.value;

      if (option.selected && visible) {
        selectedStillVisible = true;
      }
    });

    if (!selectedStillVisible) {
      select.value = "";
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
  }

  function searchInputFor(select) {
    const wrapper = select.closest(".select-search");
    return wrapper ? wrapper.querySelector(".select-search-input") : null;
  }

  function resetSearch(select) {
    const input = searchInputFor(select);
    if (input) {
      input.value = "";
    }
    refreshSelect(select, input);
  }

  function initSearchableSelect(select) {
    if (select.dataset.searchReady === "true") {
      resetSearch(select);
      return;
    }

    select.dataset.searchReady = "true";

    const optionCount = Array.from(select.options).filter(function (option) {
      return option.value;
    }).length;
    const threshold = Number(select.getAttribute("data-search-threshold") || 8);
    const shouldRenderSearch = optionCount >= threshold || select.getAttribute("data-always-searchable") === "true";
    let input = null;

    if (shouldRenderSearch) {
      const wrapper = document.createElement("div");
      wrapper.className = "select-search";

      input = document.createElement("input");
      input.type = "search";
      input.className = "select-search-input";
      input.placeholder = select.getAttribute("data-search-placeholder") || "Filtrar opciones";
      input.setAttribute("aria-label", "Filtrar opciones");

      select.parentNode.insertBefore(wrapper, select);
      wrapper.appendChild(input);
      wrapper.appendChild(select);

      input.addEventListener("input", function () {
        refreshSelect(select, input);
      });
    }

    ["data-filter-client-source", "data-filter-project-source"].forEach(function (attr) {
      const sourceId = select.getAttribute(attr);
      const source = sourceId ? document.getElementById(sourceId) : null;

      if (source) {
        source.addEventListener("change", function () {
          refreshSelect(select, input);
        });
      }
    });

    refreshSelect(select, input);
  }

  function initInternalPicker(picker) {
    const select = picker.querySelector("select");
    const trigger = picker.querySelector("[data-picker-trigger]");
    const value = picker.querySelector("[data-picker-value]");
    const dropdown = picker.querySelector("[data-picker-dropdown]");
    const search = picker.querySelector("[data-picker-search]");
    const options = picker.querySelector("[data-picker-options]");
    const selectedTags = picker.querySelector("[data-picker-selected-tags]");
    if (!select || !trigger || !value || !dropdown || !search || !options) return;
    const multiple = select.multiple;

    function normalize(text) { return text.trim().toLocaleLowerCase().replace(/\s+/g, " "); }
    function closePicker() {
      dropdown.hidden = true;
      trigger.setAttribute("aria-expanded", "false");
    }
    function renderOptions() {
      const query = normalize(search.value);
      options.replaceChildren();
      let matches = 0;
      Array.from(select.options).forEach(function (option) {
        const label = option.textContent.trim();
        if (query && !normalize(label).includes(query)) return;
        const item = document.createElement("button");
        item.type = "button";
        item.className = "category-picker-option";
        item.setAttribute("role", "option");
        item.setAttribute("aria-selected", option.selected ? "true" : "false");
        item.textContent = option.value ? label : (picker.getAttribute("data-empty-label") || "Seleccionar opción");
        item.addEventListener("click", function () {
          if (multiple) {
            option.selected = !option.selected;
          } else {
            select.value = option.value;
          }
          select.dispatchEvent(new Event("change", {bubbles: true}));
          if (!multiple) {
            closePicker();
            trigger.focus();
          } else {
            renderOptions();
            search.focus();
          }
        });
        options.appendChild(item);
        matches += 1;
      });
      if (!matches) {
        const empty = document.createElement("p");
        empty.className = "category-picker-empty";
        empty.textContent = "No se encontraron opciones";
        options.appendChild(empty);
      }
    }
    function renderSelectedTags() {
      if (!selectedTags) return;
      selectedTags.replaceChildren();
      if (!multiple) return;
      Array.from(select.selectedOptions).forEach(function (option) {
        const tag = document.createElement("button");
        tag.type = "button";
        tag.className = "category-picker-selected-tag";
        tag.textContent = option.textContent.trim() + " ×";
        tag.setAttribute("aria-label", "Quitar " + option.textContent.trim());
        tag.addEventListener("click", function (event) {
          event.preventDefault();
          event.stopPropagation();
          option.selected = false;
          select.dispatchEvent(new Event("change", {bubbles: true}));
          trigger.focus();
        });
        selectedTags.appendChild(tag);
      });
    }
    function syncPicker() {
      if (multiple) {
        const selected = Array.from(select.selectedOptions).filter(function (option) { return option.value; });
        value.textContent = selected.length
          ? selected.map(function (option) { return option.textContent.trim(); }).join(", ")
          : (picker.getAttribute("data-empty-label") || "Seleccionar opción");
      } else {
        const selected = select.options[select.selectedIndex];
        value.textContent = selected && selected.value ? selected.textContent.trim() : (picker.getAttribute("data-empty-label") || "Seleccionar opción");
      }
      renderSelectedTags();
      renderOptions();
    }
    trigger.addEventListener("click", function () {
      if (dropdown.hidden) {
        dropdown.hidden = false;
        trigger.setAttribute("aria-expanded", "true");
        search.value = "";
        renderOptions();
        search.focus();
      } else closePicker();
    });
    trigger.addEventListener("keydown", function (event) {
      if (["ArrowDown", "Enter", " "].includes(event.key)) { event.preventDefault(); if (dropdown.hidden) trigger.click(); }
      if (event.key === "Escape") closePicker();
    });
    search.addEventListener("input", renderOptions);
    search.addEventListener("keydown", function (event) {
      if (event.key === "Escape") { closePicker(); trigger.focus(); }
      if (event.key === "Enter") {
        const first = options.querySelector(".category-picker-option");
        if (first) { event.preventDefault(); first.click(); }
      }
    });
    document.addEventListener("click", function (event) { if (!picker.contains(event.target)) closePicker(); });
    select.addEventListener("change", syncPicker);
    syncPicker();
  }

  function relatedKind(select) {
    const name = select.name || select.id.replace(/^id_/, "");
    if (["contacto", "proyecto", "cotizacion", "solicitud", "punto_venta", "puntos_venta"].includes(name)) {
      return name === "puntos_venta" ? "punto_venta" : name;
    }
    return "";
  }

  function relatedEndpoint(select) {
    const kind = relatedKind(select);
    const clientSource = document.getElementById(select.getAttribute("data-filter-client-source") || "");
    const projectSource = document.getElementById(select.getAttribute("data-filter-project-source") || "");
    const clientId = clientSource ? clientSource.value : "";
    const projectId = projectSource ? projectSource.value : "";

    if (kind === "solicitud" && projectId) {
      return `/panel/ajax/proyectos/${encodeURIComponent(projectId)}/solicitudes/`;
    }
    if (!clientId) {
      return "";
    }
    if (kind === "contacto") {
      return `/panel/ajax/clientes/${encodeURIComponent(clientId)}/contactos/`;
    }
    if (kind === "proyecto") {
      return `/panel/ajax/clientes/${encodeURIComponent(clientId)}/proyectos/`;
    }
    if (kind === "cotizacion") {
      return `/panel/ajax/clientes/${encodeURIComponent(clientId)}/cotizaciones/`;
    }
    if (kind === "punto_venta") {
      return `/panel/ajax/clientes/${encodeURIComponent(clientId)}/puntos-venta/`;
    }
    if (kind === "solicitud") {
      return `/panel/ajax/clientes/${encodeURIComponent(clientId)}/solicitudes/`;
    }
    return "";
  }

  function setPlaceholder(select, label) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = label || "---------";
    select.appendChild(option);
  }

  function renderRelatedOptions(select, items, previousValue) {
    select.innerHTML = "";
    if (!select.multiple) {
      setPlaceholder(select, "---------");
    }

    items.forEach(function (item) {
      const option = document.createElement("option");
      option.value = String(item.id);
      option.textContent = item.label;
      if (item.client_id) {
        option.setAttribute("data-client-id", String(item.client_id));
      }
      if (item.project_id) {
        option.setAttribute("data-project-id", String(item.project_id));
      }
      if (item.is_principal) {
        option.setAttribute("data-principal", "true");
      }
      if (select.multiple && Array.isArray(previousValue) && previousValue.includes(String(item.id))) {
        option.selected = true;
      }
      select.appendChild(option);
    });

    if (!select.multiple && previousValue && Array.from(select.options).some(function (option) { return option.value === previousValue; })) {
      select.value = previousValue;
    } else if (!select.multiple && !previousValue && select.getAttribute("data-prefer-principal") === "true") {
      const principal = Array.from(select.options).find(function (option) {
        return option.getAttribute("data-principal") === "true";
      });
      if (principal) {
        select.value = principal.value;
      }
    }

    if (!items.length && select.options[0]) {
      select.options[0].textContent = select.getAttribute("data-empty-label") || "Sin opciones disponibles";
    }

    resetSearch(select);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function loadRelatedOptions(select, keepValue) {
    const endpoint = relatedEndpoint(select);
    const previousValue = keepValue
      ? (select.multiple ? Array.from(select.selectedOptions).map(function (option) { return option.value; }) : select.value)
      : (select.multiple ? [] : "");

    if (!endpoint) {
      select.innerHTML = "";
      if (!select.multiple) {
        setPlaceholder(select, "Selecciona un cliente primero");
      }
      resetSearch(select);
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    select.innerHTML = "";
    if (!select.multiple) {
      setPlaceholder(select, "Cargando...");
    }
    resetSearch(select);

    fetch(endpoint, { credentials: "same-origin" })
      .then(function (response) {
        if (!response.ok) {
          throw new Error("No se pudieron cargar las opciones.");
        }
        return response.json();
      })
      .then(function (data) {
        renderRelatedOptions(select, data.results || [], previousValue);
      })
      .catch(function () {
        select.innerHTML = "";
        if (!select.multiple) {
          setPlaceholder(select, "No se pudieron cargar las opciones");
        }
        resetSearch(select);
      });
  }

  document.querySelectorAll("select[data-searchable-select]").forEach(initSearchableSelect);
  document.querySelectorAll("[data-internal-picker]").forEach(initInternalPicker);

  document.querySelectorAll("select[data-filter-client-source], select[data-filter-project-source]").forEach(function (select) {
    const clientSource = document.getElementById(select.getAttribute("data-filter-client-source") || "");
    const projectSource = document.getElementById(select.getAttribute("data-filter-project-source") || "");

    if (clientSource) {
      clientSource.addEventListener("change", function () {
        loadRelatedOptions(select, false);
      });
    }

    if (projectSource) {
      projectSource.addEventListener("change", function () {
        loadRelatedOptions(select, false);
      });
    }

    const hasServerOptions = select.multiple && Array.from(select.options).some(function (option) { return option.value; });
    if (((clientSource && clientSource.value) || (projectSource && projectSource.value)) && !hasServerOptions) {
      loadRelatedOptions(select, true);
    }
  });

  function openModal(id) {
    const modal = document.getElementById(id);

    if (!modal) {
      return;
    }

    modal.classList.add("is-open");
    document.body.classList.add("modal-open");
  }

  function closeModal(modal) {
    if (!modal) {
      return;
    }

    modal.classList.remove("is-open");

    if (!document.querySelector(".modal-shell.is-open")) {
      document.body.classList.remove("modal-open");
    }
  }

  document.querySelectorAll("[data-modal-open]").forEach(function (button) {
    button.addEventListener("click", function () {
      openModal(button.getAttribute("data-modal-open"));
    });
  });

  document.querySelectorAll("[data-modal]").forEach(function (modal) {
    modal.addEventListener("click", function (event) {
      if (event.target === modal || event.target.hasAttribute("data-modal-close")) {
        closeModal(modal);
      }
    });
  });

  function initClientMenu() {
    const body = document.querySelector(".client-body");
    const toggle = document.querySelector("[data-client-menu-toggle]");
    const nav = document.querySelector("[data-client-nav]");

    if (!body || !toggle || !nav) {
      return;
    }

    function isOpen() {
      return body.classList.contains("client-menu-open");
    }

    function setState(open) {
      body.classList.toggle("client-menu-open", open);
      toggle.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    toggle.addEventListener("click", function () {
      setState(!isOpen());
    });

    nav.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", function () {
        setState(false);
      });
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        setState(false);
      }
    });

    setState(false);
  }

  function initPanelSidebar() {
    const body = document.querySelector("[data-panel-layout]");
    const toggle = document.querySelector("[data-panel-menu-toggle]");
    const overlay = document.querySelector("[data-panel-sidebar-overlay]");
    const sidebar = document.querySelector("[data-panel-sidebar]");

    if (!body || !toggle || !sidebar) {
      return;
    }

    function isOpen() {
      return body.classList.contains("panel-sidebar-open");
    }

    function setToggleState(open) {
      toggle.classList.toggle("is-open", open);
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    }

    function openSidebar() {
      body.classList.add("panel-sidebar-open");
      body.classList.remove("panel-sidebar-collapsed");
      setToggleState(true);
    }

    function closeSidebar() {
      body.classList.remove("panel-sidebar-open");
      body.classList.add("panel-sidebar-collapsed");
      setToggleState(false);
    }

    function toggleSidebar() {
      if (isOpen()) {
        closeSidebar();
      } else {
        openSidebar();
      }
    }

    toggle.addEventListener("click", toggleSidebar);

    if (overlay) {
      overlay.addEventListener("click", closeSidebar);
    }

    sidebar.querySelectorAll("a").forEach(function (link) {
      link.addEventListener("click", closeSidebar);
    });

    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape") {
        closeSidebar();
      }
    });

    closeSidebar();
  }

  function refreshTableHints() {
    document.querySelectorAll(".table-responsive").forEach(function (wrapper) {
      const table = wrapper.querySelector("table");
      if (!table) {
        wrapper.removeAttribute("data-scroll-hint");
        return;
      }
      const hasOverflow = table.scrollWidth > wrapper.clientWidth + 8;
      if (hasOverflow) {
        wrapper.setAttribute("data-scroll-hint", "true");
      } else {
        wrapper.removeAttribute("data-scroll-hint");
      }
    });
  }

  function initAlegraConnection() {
    var widget = document.querySelector("[data-alegra-connection]");
    if (!widget) return;
    var trigger = widget.querySelector(".alegra-connection-trigger");
    var details = widget.querySelector(".alegra-connection-details");
    var manual = widget.querySelector("[data-alegra-check]");
    var statusUrl = widget.getAttribute("data-status-url");
    function render(data) {
      widget.className = "alegra-connection-widget alegra-connection-" + (data.state || "unknown");
      trigger.className = "alegra-connection-trigger alegra-connection-" + (data.state || "unknown");
      widget.querySelector(".alegra-connection-label").textContent = data.label || "Verificando";
      widget.querySelector("[data-alegra-label]").textContent = data.label || "Verificando";
      widget.querySelector("[data-alegra-reason]").textContent = data.reason || "Sin detalle disponible.";
      widget.querySelector("[data-alegra-checked]").textContent = data.checked_at_display || "—";
      widget.querySelector("[data-alegra-success]").textContent = data.last_success_at_display || "—";
    }
    function fetchStatus(force) {
      if (!navigator.onLine) {
        render({state: "unknown", label: "Sin verificar", reason: "El navegador no tiene conexión con BettaApp."});
        return;
      }
      var url = statusUrl + (force ? "?force=1" : "");
      fetch(url, {credentials: "same-origin"}).then(function (response) {
        if (!response.ok) throw new Error("No se pudo consultar el estado.");
        return response.json();
      }).then(render).catch(function () {
        render({state: "unknown", label: "Sin verificar", reason: "No se pudo consultar el estado de BettaApp."});
      });
    }
    trigger.addEventListener("click", function () {
      var open = !details.hidden;
      details.hidden = open;
      trigger.setAttribute("aria-expanded", open ? "false" : "true");
    });
    manual.addEventListener("click", function () { manual.disabled = true; fetchStatus(true); setTimeout(function () { manual.disabled = false; }, 1000); });
    fetchStatus(false);
    window.setInterval(function () { fetchStatus(true); }, 300000);
  }

  initClientMenu();
  initPanelSidebar();
  initAlegraConnection();
  refreshTableHints();
  window.addEventListener("resize", refreshTableHints);

  document.querySelectorAll("[data-loading-form]").forEach(function (form) {
    form.addEventListener("submit", function () {
      const button = form.querySelector("[data-loading-button]");
      if (!button) return;
      button.disabled = true;
      button.textContent = "Consultando…";
    });
  });

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal-shell.is-open").forEach(closeModal);
    }
  });
});
