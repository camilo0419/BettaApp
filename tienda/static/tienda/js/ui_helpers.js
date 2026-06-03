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

  function relatedKind(select) {
    const name = select.name || select.id.replace(/^id_/, "");
    if (["contacto", "proyecto", "solicitud"].includes(name)) {
      return name;
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
    setPlaceholder(select, "---------");

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
      select.appendChild(option);
    });

    if (previousValue && Array.from(select.options).some(function (option) { return option.value === previousValue; })) {
      select.value = previousValue;
    } else if (!previousValue && select.getAttribute("data-prefer-principal") === "true") {
      const principal = Array.from(select.options).find(function (option) {
        return option.getAttribute("data-principal") === "true";
      });
      if (principal) {
        select.value = principal.value;
      }
    }

    if (!items.length) {
      select.options[0].textContent = select.getAttribute("data-empty-label") || "Sin opciones disponibles";
    }

    resetSearch(select);
    select.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function loadRelatedOptions(select, keepValue) {
    const endpoint = relatedEndpoint(select);
    const previousValue = keepValue ? select.value : "";

    if (!endpoint) {
      select.innerHTML = "";
      setPlaceholder(select, "Selecciona un cliente primero");
      resetSearch(select);
      select.dispatchEvent(new Event("change", { bubbles: true }));
      return;
    }

    select.innerHTML = "";
    setPlaceholder(select, "Cargando...");
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
        setPlaceholder(select, "No se pudieron cargar las opciones");
        resetSearch(select);
      });
  }

  document.querySelectorAll("select[data-searchable-select]").forEach(initSearchableSelect);

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

    if ((clientSource && clientSource.value) || (projectSource && projectSource.value)) {
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

  initClientMenu();
  initPanelSidebar();
  refreshTableHints();
  window.addEventListener("resize", refreshTableHints);

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal-shell.is-open").forEach(closeModal);
    }
  });
});
