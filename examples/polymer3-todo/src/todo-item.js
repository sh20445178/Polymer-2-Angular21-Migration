import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';
import '@polymer/polymer/lib/elements/dom-if.js';
import './todo-styles.js';

class TodoItem extends PolymerElement {
  static get is() { return 'todo-item'; }

  static get template() {
    return html`
      <style include="todo-styles">
        :host { display: flex; align-items: center; gap: 8px; }
      </style>
      <input type="checkbox" checked="[[item.done]]" on-change="_onToggle">
      <label class$="[[_labelClass(item.done)]]">[[item.title]]</label>
      <dom-if if="[[item.done]]">
        <template><span class="muted">(done)</span></template>
      </dom-if>
      <slot name="actions"></slot>
    `;
  }

  static get properties() {
    return {
      item: {
        type: Object,
        observer: '_itemChanged'
      }
    };
  }

  _labelClass(done) {
    return done ? 'done' : '';
  }

  _itemChanged(newItem, oldItem) {
    if (oldItem) {
      console.debug('item changed from', oldItem.title, 'to', newItem.title);
    }
  }

  _onToggle() {
    this.dispatchEvent(new CustomEvent('toggle', {detail: this.item}));
  }
}

customElements.define(TodoItem.is, TodoItem);
