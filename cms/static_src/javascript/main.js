import IframeResize from './components/iframe-resize';
import detailsToggle from './components/details';

import '../sass/main.scss';

function initComponent(ComponentClass) {
  const items = document.querySelectorAll(ComponentClass.selector());
  items.forEach((item) => new ComponentClass(item));
}

document.addEventListener('DOMContentLoaded', () => {
  initComponent(IframeResize);
  initComponent(detailsToggle);
});
