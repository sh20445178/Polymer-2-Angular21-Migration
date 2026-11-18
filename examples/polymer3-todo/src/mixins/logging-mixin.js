import { dedupingMixin } from '@polymer/polymer/lib/utils/mixin.js';

/** Adds a simple, prefixed logger to an element. */
export const LoggingMixin = dedupingMixin((superClass) => class extends superClass {
  static get properties() {
    return {
      logPrefix: {
        type: String,
        value: '[todo]'
      }
    };
  }

  log(...args) {
    console.log(this.logPrefix, ...args);
  }
});
