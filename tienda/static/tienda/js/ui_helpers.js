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
      if (selectedClient && optionClient !== selectedClient) {
        return false;
      }
    }
    if (projectSourceId) {
      const source = document.getElementById(projectSourceId);
      const selectedProject = source ? source.value : "";
      const optionProject = option.getAttribute("data-project-id") || "";
      if (selectedProject && optionProject !== selectedProject) {
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

  document.querySelectorAll("select[data-searchable-select]").forEach(function (select) {
    if (select.dataset.searchReady === "true") {
      return;
    }
    select.dataset.searchReady = "true";
    const wrapper = document.createElement("div");
    wrapper.className = "select-search";
    const input = document.createElement("input");
    input.type = "search";
    input.className = "select-search-input";
    input.placeholder = "Buscar...";
    input.setAttribute("aria-label", "Buscar opciones");
    select.parentNode.insertBefore(wrapper, select);
    wrapper.appendChild(input);
    wrapper.appendChild(select);
    input.addEventListener("input", function () {
      refreshSelect(select, input);
    });
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

  document.addEventListener("keydown", function (event) {
    if (event.key === "Escape") {
      document.querySelectorAll(".modal-shell.is-open").forEach(closeModal);
    }
  });
});
