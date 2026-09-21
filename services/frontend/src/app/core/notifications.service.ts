import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { AuthService } from './auth.service';
import { NotificationPage } from './models';

/**
 * Quando è stata aperta l'ultima volta la casella, in questo browser, da questo account.
 *
 * Tenuto qui invece che nel database di proposito: «letto» è una proprietà di
 * una persona davanti a uno schermo, non dell'avviso, e aggiungere una colonna
 * significherebbe un endpoint che la scrive — per un contrassegno. Il
 * compromesso è che il conto è per browser: aprire la casella sul portatile
 * lascia il telefono a mostrarli ancora come nuovi.
 *
 * L'account fa parte della chiave, altrimenti due persone che condividono un
 * computer condividerebbero il segnalibro: la seconda ad accedere troverebbe i
 * propri messaggi più vecchi già contati come visti.
 */
const SEEN_PREFIX = 'media-platform.notifications-seen';

@Injectable({ providedIn: 'root' })
export class NotificationsService {
  /** Quanti ne sono arrivati dall'ultima visita. È ciò che muove il contrassegno. */
  readonly unread = signal(0);

  private readonly http = inject(HttpClient);
  private readonly auth = inject(AuthService);

  list(page = 1, pageSize = 20): Observable<NotificationPage> {
    const params = new HttpParams()
      .set('page', String(page))
      .set('page_size', String(pageSize));
    return this.http.get<NotificationPage>('/api/notifications', { params });
  }

  /**
   * Ricalcola quanti sono i nuovi.
   *
   * Chiede un elemento solo e legge il totale: il conto è tutto ciò che serve, e
   * la pagina in sé verrebbe buttata via.
   */
  refresh(): void {
    let params = new HttpParams().set('page_size', '1');
    const since = localStorage.getItem(this.seenKey());
    if (since !== null) {
      params = params.set('since', since);
    }

    this.http.get<NotificationPage>('/api/notifications', { params }).subscribe({
      next: (page) => this.unread.set(page.total),
      // Un contrassegno non vale un messaggio d'errore: resta semplicemente com'era.
      error: () => undefined,
    });
  }

  /**
   * Segna come visti tutti quelli fino a `at`.
   *
   * L'istante arriva dall'avviso più recente a schermo, non dall'orologio: uno
   * che arriva mentre la pagina viene letta deve comunque contare come
   * new.
   */
  markSeen(at: string): void {
    localStorage.setItem(this.seenKey(), at);
    this.unread.set(0);
  }

  private seenKey(): string {
    return `${SEEN_PREFIX}.${this.auth.userId() ?? 'anonimo'}`;
  }

  /** Comodità per la casella: carica una pagina e prende nota di cosa è stato visto. */
  open(page: number): Observable<NotificationPage> {
    return this.list(page).pipe(
      tap((result) => {
        const newest = result.items[0];
        if (page === 1 && newest !== undefined) {
          this.markSeen(newest.created_at);
        }
      }),
    );
  }
}
