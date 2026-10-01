import { config } from '@hotwired/turbo';

// Show that a server request is in progress during slower page changes.
config.drive.progressBarDelay = 150;

// Keep each filtered shelf at its own position when opening a book and returning.
const shelfScrollKey = () => `shelf-scroll:${location.pathname}${location.search}`;

document.addEventListener('turbo:before-cache', () => {
  const results = document.querySelector('.book-results');
  if (results) sessionStorage.setItem(shelfScrollKey(), String(results.scrollTop));
});

document.addEventListener('turbo:load', () => {
  const results = document.querySelector('.book-results');
  const saved = results && sessionStorage.getItem(shelfScrollKey());
  if (saved) requestAnimationFrame(() => { results.scrollTop = Number(saved) || 0; });
});
