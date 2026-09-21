import { HttpErrorResponse } from '@angular/common/http';

import { codeOf, messageOf } from './errors';

/**
 * Da una chiamata fallita alla frase che legge l'utente.
 *
 * L'api-service risponde sempre {"error": {"code", "message"}} con un messaggio
 * già scritto per una persona. Qualunque altra cosa arrivi significa che la
 * richiesta non è mai arrivata fin là, e l'utente deve comunque ricevere qualcosa di leggibile.
 */
describe('messageOf', () => {
  it("mostra il messaggio che l'API ha scritto per l'utente", () => {
    const failure = new HttpErrorResponse({
      status: 409,
      error: { error: { code: 'email_taken', message: 'Questa email è già registrata' } },
    });

    expect(messageOf(failure)).toBe('Questa email è già registrata');
  });

  it('dice che il server non risponde quando la richiesta non è partita', () => {
    // Stato 0: nessuna risposta, nemmeno di errore. Rete caduta o API spenta.
    const failure = new HttpErrorResponse({ status: 0 });

    expect(messageOf(failure)).toContain('Server non raggiungibile');
  });

  it("ripiega su un messaggio generico per un errore senza forma riconoscibile", () => {
    const failure = new HttpErrorResponse({ status: 502, error: '<html>Bad Gateway</html>' });

    expect(messageOf(failure)).toBe('Si è verificato un errore imprevisto. Riprova.');
  });

  it("non esplode su qualcosa che non è nemmeno un errore HTTP", () => {
    expect(messageOf(new Error('boom'))).toBe('Si è verificato un errore imprevisto. Riprova.');
    expect(messageOf(undefined)).toBe('Si è verificato un errore imprevisto. Riprova.');
  });
});

describe('codeOf', () => {
  it('restituisce il codice macchina quando c’è', () => {
    const failure = new HttpErrorResponse({
      status: 409,
      error: { error: { code: 'already_done', message: '…' } },
    });

    expect(codeOf(failure)).toBe('already_done');
  });

  it('restituisce null quando non c’è', () => {
    expect(codeOf(new HttpErrorResponse({ status: 0 }))).toBeNull();
  });
});
