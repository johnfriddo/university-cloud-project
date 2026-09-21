import { Component, inject, signal } from '@angular/core';
import { FormBuilder, ReactiveFormsModule, Validators } from '@angular/forms';
import { Router } from '@angular/router';
import { switchMap } from 'rxjs';

import { AuthService } from '../../core/auth.service';
import { messageOf } from '../../core/errors';

type Mode = 'accedi' | 'registrati';

/** Un modulo solo per entrare e per iscriversi: i campi sono gli stessi due. */
@Component({
  selector: 'app-login',
  imports: [ReactiveFormsModule],
  templateUrl: './login.html',
  styleUrl: './login.css',
})
export class LoginPage {
  /** Rispecchia la regola che applica l'api-service, così il rifiuto arriva prima. */
  protected readonly minPasswordLength = 8;

  protected readonly mode = signal<Mode>('accedi');
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  protected readonly form = inject(FormBuilder).nonNullable.group({
    email: ['', [Validators.required, Validators.email]],
    password: ['', [Validators.required, Validators.minLength(8)]],
  });

  protected switchMode(): void {
    this.mode.update((current) => (current === 'accedi' ? 'registrati' : 'accedi'));
    this.error.set(null);
  }

  protected submit(): void {
    if (this.form.invalid || this.busy()) {
      this.form.markAllAsTouched();
      return;
    }

    const { email, password } = this.form.getRawValue();
    this.busy.set(true);
    this.error.set(null);

    // Dopo l'iscrizione l'utente è subito dentro: chiedere gli stessi due campi
    // una seconda volta sarebbe pura cerimonia.
    const request =
      this.mode() === 'registrati'
        ? this.auth
            .register(email, password)
            .pipe(switchMap(() => this.auth.login(email, password)))
        : this.auth.login(email, password);

    request.subscribe({
      next: () => {
        this.busy.set(false);
        void this.router.navigate(['/libreria']);
      },
      error: (failure: unknown) => {
        this.busy.set(false);
        this.error.set(messageOf(failure));
      },
    });
  }
}
