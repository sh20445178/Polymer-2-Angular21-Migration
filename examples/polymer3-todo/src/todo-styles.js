import '@polymer/polymer/polymer-element.js';

const $_documentContainer = document.createElement('template');
$_documentContainer.innerHTML = `<dom-module id="todo-styles">
  <template>
    <style>
      .muted { color: #9e9e9e; }
      .done { text-decoration: line-through; }
    </style>
  </template>
</dom-module>`;
document.head.appendChild($_documentContainer.content);
