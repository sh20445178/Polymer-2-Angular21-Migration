import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';
import '@polymer/paper-button/paper-button.js';
import { LoggingMixin } from './mixins/logging-mixin.js';

class TodoInput extends LoggingMixin(PolymerElement) {
  static get is() { return 'todo-input'; }

  static get template() {
    return html`
      <style>
        :host { display: flex; gap: 8px; }
        input { flex: 1; }
      </style>
      <input id="field" placeholder="What needs to be done?" value="{{text::input}}" on-keydown="_onKey">
      <paper-button raised on-tap="_submit" disabled="[[!text]]">Add</paper-button>
    `;
  }

  static get properties() {
    return {
      text: {
        type: String,
        value: ''
      }
    };
  }

  _onKey(e) {
    if (e.key === 'Enter') {
      this._submit();
    }
  }

  _submit() {
    const title = (this.text || '').trim();
    if (!title) {
      return;
    }
    this.log('adding', title);
    this.dispatchEvent(new CustomEvent('todo-added', {
      detail: {title: title},
      bubbles: true,
      composed: true
    }));
    this.text = '';
    this.$.field.focus();
  }
}

customElements.define(TodoInput.is, TodoInput);
