import { PolymerElement, html } from '@polymer/polymer/polymer-element.js';
import '@polymer/polymer/lib/elements/dom-repeat.js';
import './todo-input.js';
import './todo-item.js';
import './todo-styles.js';

class TodoApp extends PolymerElement {
  static get template() {
    return html`
      <style include="todo-styles">
        :host {
          display: block;
          max-width: 480px;
          margin: 24px auto;
        }
        ul { list-style: none; padding: 0; }
      </style>

      <h1>[[heading]]</h1>
      <todo-input on-todo-added="_addTodo"></todo-input>

      <ul id="list">
        <template is="dom-repeat" items="[[todos]]" as="todo">
          <li>
            <todo-item item="[[todo]]" on-toggle="_toggle">
              <button slot="actions" on-click="_remove">x</button>
            </todo-item>
          </li>
        </template>
      </ul>

      <p class="muted">[[remaining]] of [[todos.length]] remaining</p>
      <button on-click="clearCompleted" hidden$="[[!hasCompleted]]">Clear completed</button>
    `;
  }

  static get properties() {
    return {
      heading: {
        type: String,
        value: 'Todos'
      },
      todos: {
        type: Array,
        value() {
          return [{title: 'Learn Angular signals', done: false}];
        }
      },
      remaining: {
        type: Number,
        computed: '_remaining(todos.*)'
      },
      hasCompleted: {
        type: Boolean,
        computed: '_hasCompleted(todos.*)'
      },
      busy: {
        type: Boolean,
        value: false,
        reflectToAttribute: true
      }
    };
  }

  static get observers() {
    return ['_todosChanged(todos.splices)'];
  }

  connectedCallback() {
    super.connectedCallback();
    this.addEventListener('keyup', this._onKeyUp);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    this.removeEventListener('keyup', this._onKeyUp);
  }

  _addTodo(e) {
    this.push('todos', {title: e.detail.title, done: false});
  }

  _toggle(e) {
    const index = this.todos.indexOf(e.detail);
    this.set('todos.' + index + '.done', !e.detail.done);
  }

  _remove(e) {
    const index = e.model.index;
    this.splice('todos', index, 1);
  }

  _remaining() {
    return this.todos.filter((t) => !t.done).length;
  }

  _hasCompleted() {
    return this.todos.some((t) => t.done);
  }

  _todosChanged() {
    this.busy = false;
    this.$.list.scrollTop = 0;
  }

  _onKeyUp(e) {
    if (e.key === 'Delete') {
      this.clearCompleted();
    }
  }

  clearCompleted() {
    this.todos = this.todos.filter((t) => !t.done);
    this.dispatchEvent(new CustomEvent('todos-cleared', {detail: {count: this.todos.length}}));
  }
}

customElements.define('todo-app', TodoApp);
