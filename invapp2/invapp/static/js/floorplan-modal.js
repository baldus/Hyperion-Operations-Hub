(function () {
  function closest(el, selector) {
    while (el && el !== document) {
      if (el.matches && el.matches(selector)) return el;
      el = el.parentNode;
    }
    return null;
  }

  function openModal(modal) {
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add('floorplan-modal-open');
  }

  function closeModal(modal) {
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove('floorplan-modal-open');
  }

  document.addEventListener('click', function (e) {
    const openBtn = closest(e.target, '[data-floorplan-open]');
    if (openBtn) {
      const panel = closest(openBtn, '.floorplan-panel');
      const modal = panel ? panel.querySelector('[data-floorplan-modal]') : null;
      openModal(modal);
      e.preventDefault();
      return;
    }

    const closeEl = closest(e.target, '[data-floorplan-close]');
    if (closeEl) {
      const modal = closest(closeEl, '[data-floorplan-modal]');
      closeModal(modal);
      e.preventDefault();
      return;
    }
  });

  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    const modal = document.querySelector('[data-floorplan-modal]:not([hidden])');
    if (modal) closeModal(modal);
  });
})();
