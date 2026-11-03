import { config } from '@hotwired/turbo';
import { initBookList, saveBookList } from './catalog.js';

// Show that a server request is in progress during slower page changes.
config.drive.progressBarDelay = 150;

const mobileShelf = window.matchMedia('(max-width: 760px)');

function syncCatalogControls() {
  const controls = document.querySelector('.catalog-controls');
  if (controls) controls.open = !mobileShelf.matches;
}

document.addEventListener('DOMContentLoaded', syncCatalogControls);
mobileShelf.addEventListener('change', syncCatalogControls);

document.addEventListener('turbo:before-cache', () => {
  saveBookList();
});

document.addEventListener('turbo:load', () => {
  syncCatalogControls();
  initBookList();
});
