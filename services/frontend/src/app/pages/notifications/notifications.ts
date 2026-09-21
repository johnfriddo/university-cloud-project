import { DatePipe } from '@angular/common';
import { Component, inject, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import { messageOf } from '../../core/errors';
import { Notification } from '../../core/models';
import { NotificationsService } from '../../core/notifications.service';

/**
 * La casella.
 *
 * In locale questo è l'unico posto in cui un avviso raggiunge davvero una
 * persona: nello stack non c'è nessun server di posta, quindi il
 * notification-service scrive il proprio messaggio nel registro e nel database,
 * e questa pagina è ciò che lo rende leggibile. Su AWS lo stesso messaggio esce
 * anche da SNS, e i due canali convivono — quello che segue l'utente fuori, e
 * questo per quando torna.
 */
@Component({
  selector: 'app-notifications',
  imports: [DatePipe, RouterLink],
  templateUrl: './notifications.html',
  styleUrl: './notifications.css',
})
export class NotificationsPage {
  protected readonly items = signal<Notification[]>([]);
  protected readonly total = signal(0);
  protected readonly page = signal(1);
  protected readonly pages = signal(1);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  private readonly service = inject(NotificationsService);

  constructor() {
    this.load(1);
  }

  protected load(page: number): void {
    this.loading.set(true);
    this.error.set(null);

    this.service.open(page).subscribe({
      next: (result) => {
        this.items.set(result.items);
        this.total.set(result.total);
        this.page.set(result.page);
        this.pages.set(Math.max(1, result.pages));
        this.loading.set(false);
      },
      error: (failure: unknown) => {
        this.error.set(messageOf(failure));
        this.loading.set(false);
      },
    });
  }

  /** Lo stesso linguaggio della libreria: quadrato vuoto pronta, quadrato pieno fallita. */
  protected marker(notification: Notification): string {
    return notification.event === 'asset.processed' ? '□' : '■';
  }
}
