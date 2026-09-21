import { HttpErrorResponse } from '@angular/common/http';

import { ApiError } from './models';

/**
 * Il messaggio da mettere davanti all'utente.
 *
 * L'api-service risponde sempre {"error": {"code": …, "message": …}} e il
 * messaggio è già scritto per una persona, in italiano. Qualunque altra cosa
 * significa che la richiesta non è mai arrivata fin là.
 */
export function messageOf(error: unknown): string {
  if (error instanceof HttpErrorResponse) {
    const body = error.error as ApiError | null;
    if (body?.error?.message) {
      return body.error.message;
    }
    if (error.status === 0) {
      return 'Server non raggiungibile. Controlla la connessione e riprova.';
    }
  }
  return 'Si è verificato un errore imprevisto. Riprova.';
}

/** La metà leggibile da una macchina, per quando chi chiama deve decidere in base a quella. */
export function codeOf(error: unknown): string | null {
  if (error instanceof HttpErrorResponse) {
    const body = error.error as ApiError | null;
    return body?.error?.code ?? null;
  }
  return null;
}
