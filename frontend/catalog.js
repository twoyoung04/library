const shelfStates = new Map();
let activeShelf = null;

const shelfKey = () => `${location.pathname}${location.search}`;
const numberOrNull = (value) => value ? Number(value) : null;

class VirtualBookList {
  constructor(results, saved) {
    this.results = results;
    this.grid = results.querySelector('.book-grid');
    this.firstPage = Array.from(this.grid.children, card => card.outerHTML);
    this.fingerprint = this.firstPage.join('');
    this.total = Number(results.dataset.total);
    const canRestore = saved && saved.total === this.total
      && saved.fingerprint === this.fingerprint;
    this.cards = canRestore ? saved.cards.slice() : this.firstPage.slice();
    this.nextPage = canRestore ? saved.nextPage : numberOrNull(results.dataset.nextPage);
    this.restoreTop = canRestore ? saved.scrollTop : 0;
    this.loading = false;
    this.waitForUserScroll = false;
    this.failures = 0;
    this.destroyed = false;
    this.abort = new AbortController();
    this.windowStart = 0;
    this.windowEnd = this.firstPage.length;
    this.frame = 0;
    this.retryTimer = 0;

    this.canvas = document.createElement('div');
    this.canvas.className = 'book-virtual-canvas';
    this.status = document.createElement('div');
    this.status.className = 'catalog-scroll-status';
    this.status.setAttribute('role', 'status');
    this.status.setAttribute('aria-live', 'polite');
    this.grid.before(this.canvas);
    this.canvas.append(this.grid);
    this.canvas.after(this.status);
    this.grid.classList.add('is-virtual');
    this.results.classList.add('is-enhanced');

    this.onScroll = this.onScroll.bind(this);
    this.onUserScroll = this.onUserScroll.bind(this);
    this.onResize = this.onResize.bind(this);
    this.onKeyDown = this.onKeyDown.bind(this);
    this.results.addEventListener('scroll', this.onScroll, { passive: true });
    this.results.addEventListener('wheel', this.onUserScroll, { passive: true });
    this.results.addEventListener('touchmove', this.onUserScroll, { passive: true });
    this.results.addEventListener('pointermove', this.onUserScroll, { passive: true });
    this.results.addEventListener('keydown', this.onKeyDown);
    this.resizeObserver = new ResizeObserver(this.onResize);
    this.resizeObserver.observe(this.results);
    this.measure();
    this.canvas.style.height = `${this.totalHeight()}px`;
    this.results.scrollTop = this.restoreTop;
    this.render();
    this.maybeLoad();
  }

  measure() {
    this.columns = matchMedia('(max-width: 760px)').matches ? 1
      : matchMedia('(max-width: 1100px)').matches ? 2 : 3;
    const styles = getComputedStyle(this.grid);
    this.rowHeight = parseFloat(styles.getPropertyValue('--book-row-height')) || 146;
    this.gap = parseFloat(styles.rowGap) || 0;
    this.stride = this.rowHeight + this.gap;
  }

  onResize() {
    if (this.destroyed) return;
    const firstVisible = Math.floor(this.results.scrollTop / this.stride) * this.columns;
    const oldColumns = this.columns;
    this.measure();
    if (oldColumns !== this.columns) {
      this.results.scrollTop = Math.floor(firstVisible / this.columns) * this.stride;
    }
    this.render();
    this.maybeLoad();
  }

  onScroll() {
    if (this.frame || this.destroyed) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      this.render();
      this.maybeLoad();
    });
  }

  onUserScroll(event) {
    if (event.type === 'pointermove' && !event.buttons) return;
    this.waitForUserScroll = false;
    this.maybeLoad();
  }

  totalHeight() {
    const rows = Math.ceil(this.cards.length / this.columns);
    return rows ? rows * this.stride - this.gap : 0;
  }

  setStatus(message) {
    if (this.status.textContent !== message) this.status.textContent = message;
  }

  createCard(index) {
    const template = document.createElement('template');
    template.innerHTML = this.cards[index];
    const card = template.content.firstElementChild;
    card.dataset.index = String(index);
    return card;
  }

  updateWindow(start, end) {
    const oldStart = this.windowStart;
    const oldEnd = this.windowEnd;
    if (start >= oldEnd || end <= oldStart) {
      this.grid.replaceChildren(...Array.from({ length: end - start }, (_, offset) =>
        this.createCard(start + offset)));
    } else {
      for (let index = oldStart; index < start; index++) this.grid.firstElementChild.remove();
      for (let index = end; index < oldEnd; index++) this.grid.lastElementChild.remove();
      if (start < oldStart) {
        const before = document.createDocumentFragment();
        for (let index = start; index < oldStart; index++) before.append(this.createCard(index));
        this.grid.prepend(before);
      }
      if (end > oldEnd) {
        const after = document.createDocumentFragment();
        for (let index = oldEnd; index < end; index++) after.append(this.createCard(index));
        this.grid.append(after);
      }
    }
    this.windowStart = start;
    this.windowEnd = end;
  }

  render() {
    const canvasHeight = `${this.totalHeight()}px`;
    if (this.canvas.style.height !== canvasHeight) this.canvas.style.height = canvasHeight;
    const rows = Math.ceil(this.cards.length / this.columns);
    const viewport = this.results.clientHeight;
    const top = this.results.scrollTop;
    // Keep the visible screen plus exactly two viewport heights on each side.
    const startRow = Math.max(0, Math.floor((top - 2 * viewport) / this.stride));
    const endRow = Math.min(rows, Math.ceil((top + 3 * viewport) / this.stride));
    const start = startRow * this.columns;
    const end = Math.min(this.cards.length, endRow * this.columns);
    if (start !== this.windowStart || end !== this.windowEnd) this.updateWindow(start, end);
    const gridTop = `${startRow * this.stride}px`;
    if (this.grid.style.top !== gridTop) this.grid.style.top = gridTop;
    if (this.results.dataset.loadedCount !== String(this.cards.length)) {
      this.results.dataset.loadedCount = String(this.cards.length);
    }
    if (!this.loading && !this.failures) {
      this.setStatus(this.nextPage ? '' : '已经到底了');
    }
  }

  maybeLoad() {
    if (!this.nextPage || this.loading || this.destroyed || this.failures >= 3
        || this.waitForUserScroll) return;
    const remaining = this.totalHeight() - this.results.scrollTop - this.results.clientHeight;
    if (remaining <= this.results.clientHeight * 2) this.loadNext();
  }

  async loadNext() {
    if (!this.nextPage || this.loading || this.destroyed) return false;
    const requestedPage = this.nextPage;
    this.loading = true;
    this.setStatus('正在加载书目…');
    try {
      const url = new URL(location.href);
      url.searchParams.set('page', requestedPage);
      url.searchParams.set('fragment', 'cards');
      const response = await fetch(url, {
        credentials: 'same-origin',
        headers: { Accept: 'application/json' },
        signal: this.abort.signal,
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      if (data.page !== requestedPage) throw new Error('Unexpected page');
      const template = document.createElement('template');
      template.innerHTML = data.cards;
      const additions = Array.from(template.content.children)
        .filter(card => card.classList.contains('book-card'))
        .map(card => card.outerHTML);
      if (!additions.length) throw new Error('Empty page');
      if (this.destroyed) return false;
      this.cards.push(...additions);
      this.nextPage = numberOrNull(data.next_page);
      this.waitForUserScroll = true;
      this.failures = 0;
      this.setStatus('');
      this.render();
      return true;
    } catch (error) {
      if (this.destroyed || error.name === 'AbortError') return false;
      this.failures += 1;
      this.setStatus(this.failures >= 3
        ? '书目加载失败，请刷新页面重试' : '书目加载失败，正在重试…');
      if (this.failures < 3) {
        this.retryTimer = setTimeout(() => {
          this.retryTimer = 0;
          this.maybeLoad();
        }, 1000 * 2 ** this.failures);
      }
      return false;
    } finally {
      this.loading = false;
    }
  }

  async onKeyDown(event) {
    if (['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', 'Home', 'End', ' '].includes(event.key)) {
      this.waitForUserScroll = false;
      requestAnimationFrame(() => this.maybeLoad());
    }
    if (event.key !== 'Tab') return;
    const card = document.activeElement.closest?.('.book-card');
    if (!card || !this.grid.contains(card)) return;
    const index = Number(card.dataset.index);
    const next = index + (event.shiftKey ? -1 : 1);
    if (next < 0 || next < this.cards.length && next >= Number(this.grid.firstElementChild?.dataset.index)
        && next <= Number(this.grid.lastElementChild?.dataset.index)) return;
    if (next >= this.cards.length && !this.nextPage) return;
    event.preventDefault();
    if (next >= this.cards.length && !await this.loadNext()) return;
    const rowTop = Math.floor(next / this.columns) * this.stride;
    if (rowTop < this.results.scrollTop || rowTop + this.stride > this.results.scrollTop + this.results.clientHeight) {
      this.results.scrollTop = Math.max(0, rowTop - this.results.clientHeight / 3);
    }
    this.render();
    this.grid.querySelector(`[data-index="${next}"]`)?.focus();
  }

  save() {
    shelfStates.delete(shelfKey());
    shelfStates.set(shelfKey(), {
      cards: this.cards,
      nextPage: this.nextPage,
      scrollTop: this.results.scrollTop,
      total: this.total,
      fingerprint: this.fingerprint,
    });
    if (shelfStates.size > 3) shelfStates.delete(shelfStates.keys().next().value);
  }

  destroy(forCache = false) {
    this.destroyed = true;
    this.abort.abort();
    clearTimeout(this.retryTimer);
    cancelAnimationFrame(this.frame);
    this.resizeObserver.disconnect();
    this.results.removeEventListener('scroll', this.onScroll);
    this.results.removeEventListener('wheel', this.onUserScroll);
    this.results.removeEventListener('touchmove', this.onUserScroll);
    this.results.removeEventListener('pointermove', this.onUserScroll);
    this.results.removeEventListener('keydown', this.onKeyDown);
    if (forCache) {
      this.grid.classList.remove('is-virtual');
      this.grid.style.top = '';
      this.canvas.before(this.grid);
      this.grid.innerHTML = this.firstPage.join('');
      this.results.classList.remove('is-enhanced');
      this.canvas.remove();
      this.status.remove();
      this.results.scrollTop = 0;
    }
  }
}

export function saveBookList() {
  if (!activeShelf) return;
  activeShelf.save();
  activeShelf.destroy(true);
  activeShelf = null;
}

export function initBookList() {
  if (activeShelf) activeShelf.destroy();
  activeShelf = null;
  const results = document.querySelector('.book-results');
  if (!results?.querySelector('.book-grid') || results.dataset.page !== '1'
      || !('ResizeObserver' in window)) return;
  activeShelf = new VirtualBookList(results, shelfStates.get(shelfKey()));
}
