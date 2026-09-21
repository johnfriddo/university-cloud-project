import { Component, effect, inject, untracked } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';

import { AuthService } from './core/auth.service';
import { NotificationsService } from './core/notifications.service';

@Component({
  selector: 'app-root',
  imports: [RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './app.html',
  styleUrl: './app.css',
})
export class App {
  protected readonly auth = inject(AuthService);
  protected readonly notifications = inject(NotificationsService);

  private readonly router = inject(Router);

  constructor() {
    // A un ricaricamento il token viene recuperato dalla memoria ma l'identità che
    // gli sta dietro no: la si chiede una volta, il che conferma anche che il server la accetta ancora.
    effect(() => {
      if (this.auth.isAuthenticated() && untracked(() => this.auth.user()) === null) {
        this.auth.loadUser().subscribe({ error: () => undefined });
        this.notifications.refresh();
      }
    });
  }

  protected logout(): void {
    this.auth.logout();
    void this.router.navigate(['/accedi']);
  }
}
