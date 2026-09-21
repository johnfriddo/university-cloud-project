import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';

import { AuthService } from './auth.service';

/**
 * Attacca il token di sessione alla nostra API, e soltanto a quella.
 *
 * Il caricamento va direttamente all'object storage con un link firmato, che
 * porta già la propria firma nella stringa di interrogazione. Aggiungere
 * un'intestazione Authorization a quella richiesta farebbe vedere all'archivio
 * due meccanismi di autenticazione in concorrenza, e la rifiuterebbe.
 */
export const authInterceptor: HttpInterceptorFn = (request, next) => {
  const auth = inject(AuthService);
  const router = inject(Router);

  const isOurApi = request.url.startsWith('/api');
  const token = auth.token;

  const outgoing =
    isOurApi && token
      ? request.clone({ setHeaders: { Authorization: `Bearer ${token}` } })
      : request;

  return next(outgoing).pipe(
    catchError((error: unknown) => {
      // Un token scaduto o revocato: la sessione finisce qui invece di lasciare
      // fallire una per una tutte le chiamate successive.
      if (isOurApi && error instanceof HttpErrorResponse && error.status === 401) {
        auth.logout();
        void router.navigate(['/accedi']);
      }
      return throwError(() => error);
    }),
  );
};
