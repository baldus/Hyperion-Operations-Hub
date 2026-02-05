document.addEventListener('DOMContentLoaded', function () {
  const modal = document.querySelector('[data-floorplan-modal]');
  if (!modal) return;

  const openTrigger = document.querySelector('[data-floorplan-modal-open]');
  const closeTriggers = modal.querySelectorAll('[data-floorplan-modal-close]');

  const closeModal = () => {
    modal.hidden = true;
    document.body.classList.remove('floorplan-modal-open');
  };

  const openModal = () => {
    modal.hidden = false;
    document.body.classList.add('floorplan-modal-open');
  };

  if (openTrigger) {
    openTrigger.addEventListener('click', openModal);
  }

  closeTriggers.forEach((trigger) => {
    trigger.addEventListener('click', closeModal);
  });

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && !modal.hidden) {
      closeModal();
    }
  });
});
