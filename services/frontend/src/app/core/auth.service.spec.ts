import { provideHttpClient } from '@angular/common/http';
import { provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';

import { AuthService } from './auth.service';

const TOKEN_KEY = 'media-platform.token';
const USER_ID = '11111111-2222-3333-4444-555555555555';

/**
 * Un token fatto come quelli che emette l'api-service.
 *
 * La firma è finta di proposito: il browser non la verifica mai — può farlo
 * solo il server, che ha il segreto. Quello che il browser legge è il
 * contenuto, che chiunque sa decodificare.
 */
function token(expiresInSeconds: number): string {
  const encode = (value: object) =>
    btoa(JSON.stringify(value)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

  const now = Math.floor(Date.now() / 1000);
  return [
    encode({ alg: 'HS256', typ: 'JWT' }),
    encode({ sub: USER_ID, iat: now, exp: now + expiresInSeconds }),
    'firma-finta',
  ].join('.');
}

function startService(): AuthService {
  TestBed.configureTestingModule({
    providers: [provideHttpClient(), provideHttpClientTesting()],
  });
  return TestBed.inject(AuthService);
}

describe('AuthService', () => {
  beforeEach(() => localStorage.clear());

  it('riprende la sessione dopo una ricarica della pagina', () => {
    localStorage.setItem(TOKEN_KEY, token(3600));

    const auth = startService();

    expect(auth.isAuthenticated()).toBe(true);
  });

  it('butta un token già scaduto invece di aprire una sessione morta', () => {
    // Senza questo controllo l'applicazione si aprirebbe «dentro», e la prima
    // chiamata all'API la rimanderebbe al login con un errore.
    localStorage.setItem(TOKEN_KEY, token(-60));

    const auth = startService();

    expect(auth.isAuthenticated()).toBe(false);
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("legge chi è l'utente direttamente dal token", () => {
    // È ciò che separa il conteggio degli avvisi fra due persone che usano lo
    // stesso browser: uno dei difetti trovati nell'audit.
    localStorage.setItem(TOKEN_KEY, token(3600));

    const auth = startService();

    expect(auth.userId()).toBe(USER_ID);
  });

  it('non inventa un utente da un token illeggibile', () => {
    localStorage.setItem(TOKEN_KEY, 'non-un-jwt');

    const auth = startService();

    expect(auth.userId()).toBeNull();
  });

  it("all'uscita dimentica tutto", () => {
    localStorage.setItem(TOKEN_KEY, token(3600));
    const auth = startService();

    auth.logout();

    expect(auth.isAuthenticated()).toBe(false);
    expect(auth.userId()).toBeNull();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });
});
