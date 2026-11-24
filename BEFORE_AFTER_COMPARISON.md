# Before & After Comparison

## Error 1: TS2554 - Expected 2 arguments, but got 1

### Before
```typescript
// student-profile-detail.ts
constructor() {
  let previous_studentId: any;
  effect(() => {
    const studentId = this.studentId();
    if (studentId === undefined && previous_studentId === undefined) return;
    untracked(() => this._load(studentId, previous_studentId));
    previous_studentId = studentId;
  });
}

ngOnInit(): void {
  this._refresh = () => this._load(this.studentId());  // ❌ Only 1 argument!
  window.addEventListener(STUDENTS_CHANGED, this._refresh);
  window.addEventListener('storage', this._refresh);
}

_load(id, oldId) {  // Expects 2 arguments
  this.student.set(id ? StudentStore.get(id) : null);
  if (oldId !== undefined && id !== oldId) {
    // reset form...
  }
}
```

### After
```typescript
// student-profile-detail.ts
constructor() {
  let previous_studentId: any;
  effect(() => {
    const studentId = this.studentId();
    if (studentId === undefined && previous_studentId === undefined) return;
    untracked(() => this._load(studentId, previous_studentId));
    previous_studentId = studentId;
  });
}

ngOnInit(): void {
  this._refresh = () => {
    this._load(this.studentId(), undefined);  // ✅ Both arguments passed
  };
  window.addEventListener(STUDENTS_CHANGED, this._refresh);
  window.addEventListener('storage', this._refresh);
}

_load(id: any, oldId?: any) {  // ✅ oldId is optional
  this.student.set(id ? StudentStore.get(id) : null);
  if (oldId !== undefined && id !== oldId) {
    // reset form...
  }
}
```

---

## Error 2: TS2304 - Cannot find name 'STUDENTS_CHANGED'

### Before
```typescript
// student-profile-detail.ts (top of file)
import { ChangeDetectionStrategy, Component, effect, input, model, OnDestroy, OnInit, untracked } from '@angular/core';

/**
 * Migrated from Polymer element <student-profile-detail>...
 */
@Component({...})
export class StudentProfileDetail implements OnInit, OnDestroy {
  // ...
  ngOnInit(): void {
    this._refresh = () => this._load(this.studentId());
    window.addEventListener(STUDENTS_CHANGED, this._refresh);  // ❌ Undefined!
    // ...
  }
}
```

### After
```typescript
// student-profile-detail.ts (top of file)
import { ChangeDetectionStrategy, Component, effect, input, model, OnDestroy, OnInit, untracked } from '@angular/core';

const STUDENTS_CHANGED = 'students-changed';  // ✅ Defined at module level

declare const StudentStore: any;              // ✅ External dependency declared
declare const initials: (student: any) => string;
declare const formatCurrency: (value: number) => string;
declare const formatDate: (iso: string, withTime?: boolean) => string;

/**
 * Migrated from Polymer element <student-profile-detail>...
 */
@Component({...})
export class StudentProfileDetail implements OnInit, OnDestroy {
  // ...
  ngOnInit(): void {
    this._refresh = () => {
      this._load(this.studentId(), undefined);
    };
    window.addEventListener(STUDENTS_CHANGED, this._refresh);  // ✅ Defined!
    // ...
  }
}
```

---

## Error 3: Undefined Constants in student-registration.ts

### Before
```typescript
// student-registration.ts
export class StudentRegistration {
  // ...
  readonly form = model<any>(emptyForm);        // ❌ emptyForm not defined
  
  _validate(f) {
    // ...
    if (!EMAIL_RE.test(f.email.trim())) { }     // ❌ EMAIL_RE not defined
    // ...
    if (!PHONE_RE.test(f.phone.trim())) { }     // ❌ PHONE_RE not defined
    // ...
  }
}
```

### After
```typescript
// student-registration.ts
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;  // ✅ Defined
const PHONE_RE = /^\d{10}$/;                      // ✅ Defined

const emptyForm = () => ({                        // ✅ Defined as factory function
  firstName: '',
  lastName: '',
  email: '',
  phone: '',
  dateOfBirth: '',
  course: '',
  year: '',
  initialDeposit: '',
});

declare const StudentStore: any;                  // ✅ External dependency declared

export class StudentRegistration {
  // ...
  readonly form = model<any>(emptyForm());       // ✅ Properly initialized
  
  _validate(f: any) {                            // ✅ Type annotation
    // ...
    if (!EMAIL_RE.test(f.email.trim())) { }      // ✅ Now defined
    // ...
    if (!PHONE_RE.test(f.phone.trim())) { }      // ✅ Now defined
    // ...
  }
}
```

---

## Error 4: Method Binding Issue in student-wallet-app.ts

### Before
```typescript
// student-wallet-app.ts
export class StudentWalletApp implements OnInit, OnDestroy {
  // ...
  ngOnInit(): void {
    this._onHashChange = this._onHashChange.bind(this);  // ❌ Redundant binding
    window.addEventListener('hashchange', this._onHashChange);
    this._onHashChange();
  }

  ngOnDestroy(): void {
    window.removeEventListener('hashchange', this._onHashChange);
  }

  _onHashChange() {                              // ❌ Regular method loses `this`
    const [page, id] = (window.location.hash.slice(1) || 'register').split('/');
    if (page === 'profile' && id) {
      this.studentId.set(decodeURIComponent(id)); // May fail if `this` is lost
      this.page.set('profile');
    } else {
      this.page.set(page === 'profiles' ? 'profiles' : 'register');
    }
    window.scrollTo(0, 0);
  }
}
```

### After
```typescript
// student-wallet-app.ts
export class StudentWalletApp implements OnInit, OnDestroy {
  // ...
  _onHashChange = () => {                       // ✅ Arrow function property
    const [page, id] = (window.location.hash.slice(1) || 'register').split('/');
    if (page === 'profile' && id) {
      this.studentId.set(decodeURIComponent(id)); // ✅ `this` always available
      this.page.set('profile');
    } else {
      this.page.set(page === 'profiles' ? 'profiles' : 'register');
    }
    window.scrollTo(0, 0);
  };

  ngOnInit(): void {
    window.addEventListener('hashchange', this._onHashChange);  // ✅ No binding needed
    this._onHashChange();
  }

  ngOnDestroy(): void {
    window.removeEventListener('hashchange', this._onHashChange);  // ✅ Same reference
  }
}
```

---

## Summary of Patterns Fixed

| Issue | Root Cause | Solution |
|-------|-----------|----------|
| **TS2554** | Method called with insufficient arguments | Made parameters optional and updated call sites |
| **TS2304** | Missing constant definitions | Defined constants at module level |
| **Undefined external deps** | Missing `declare const` statements | Added `declare const` for runtime-provided globals |
| **Missing initializers** | Used function reference instead of calling it | Changed `emptyForm` → `emptyForm()` |
| **Binding issues** | Regular method loses `this` context | Used arrow function property instead |

These fixes align with Angular 21 best practices and maintain compatibility with the Polymer-to-Angular migration tool's output.
