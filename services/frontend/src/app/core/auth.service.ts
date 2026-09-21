import { HttpClient } from '@angular/common/http';
import { Injectable, computed, inject, signal } from '@angular/core';
import { Observable, tap } from 'rxjs';

import { LoginResponse, User } from './models';

/**
 * La sessione dell'utente corrente.
 *
 * Il token vive in localStorage, così ricaricare la pagina non fa uscire
 * l'utente. Quella scelta lo espone a un attacco cross-site scripting:
 * l'alternativa robusta è un cookie HttpOnly, che richiederebbe all'API di
 * gestire i cookie e la protezione CSRF. È un compromesso deliberato, scritto
 * nel README invece che lasciato implicito.
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private static readonly TOKEN_KEY = 'media-platform.token';

  private readonly http = inject(HttpClient);
  private readonly tokenSignal = signal<string | null>(readStoredToken());

  readonly user = signal<User | null>(null);
  readonly isAuthenticated = computed(() => this.tokenSignal() !== null);

  /**
   * Di chi è il token, letto direttamente da lui.
   *
   * Disponibile prima che /auth/me risponda, ed è questo che lo rende utilizzabile
   * come chiave per tutto ciò che questo browser ricorda per utente.
   */
  readonly userId = computed(() => subjectOf(this.tokenSignal()));

  get token(): string | null {
    return this.tokenSignal();
  }

  register(email: string, password: string): Observable<User> {
    return this.http.post<User>('/api/auth/register', { email, password });
  }

  login(email: string, password: string): Observable<LoginResponse> {
    return this.http.post<LoginResponse>('/api/auth/login', { email, password }).pipe(
      tap((response) => {
        localStorage.setItem(AuthService.TOKEN_KEY, response.access_token);
        this.tokenSignal.set(response.access_token);
      }),
    );
  }

  /** Di chi è il token. Dimostra anche che il token è ancora accettato. */
  loadUser(): Observable<User> {
    return this.http.get<User>('/api/auth/me').pipe(tap((user) => this.user.set(user)));
  }

  logout(): void {
    localStorage.removeItem(AuthService.TOKEN_KEY);
    this.tokenSignal.set(null);
    this.user.set(null);
  }
}

/**
 * Legge il token salvato, buttandolo via se è già scaduto.
 *
 * Il contenuto di un JWT è leggibile da chiunque: guardarne la scadenza non
 * richiede nessun segreto. Controllarla qui evita di aprire l'applicazione su
 * una sessione che il server rifiuterà alla prima chiamata.
 */
function readStoredToken(): string | null {
  const token = localStorage.getItem('media-platform.token');
  if (!token) {
    return null;
  }

  const expiry = expiryOf(token);
  if (expiry !== null && expiry <= Date.now()) {
    localStorage.removeItem('media-platform.token');
    return null;
  }
  return token;
}

function expiryOf(token: string): number | null {
  const exp = payloadOf(token)?.['exp'];
  return typeof exp === 'number' ? exp * 1000 : null;
}

function subjectOf(token: string | null): string | null {
  if (token === null) {
    return null;
  }
  const sub = payloadOf(token)?.['sub'];
  return typeof sub === 'string' ? sub : null;
}

/**
 * Le informazioni che il token porta con sé.
 *
 * Leggibili da chiunque — un JWT non nasconde niente, dimostra solo di non
 * essere stato alterato — quindi non c'è nessun segreto in gioco. Leggerle qui
 * risparmia un viaggio per fatti che il client ha già; ciò che conta lo ricontrolla comunque il server.
 */
function payloadOf(token: string): Record<string, unknown> | null {
  const parts = token.split('.');
  if (parts.length !== 3) {
    return null;
  }
  try {
    return JSON.parse(atob(parts[1].replace(/-/g, '+').replace(/_/g, '/')));
  } catch {
    return null;
  }
}
