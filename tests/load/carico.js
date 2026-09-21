// Il test di carico: immagini vere, caricate come le caricherebbe un browser.
//
//   deploy/cluster/load.sh                 il carico previsto dalle specifiche
//   deploy/cluster/load.sh oltre           abbastanza da far scattare l'autoscaler
//
// Non si lancia a mano: `load.sh` prepara l'immagine, misura la coda e le
// repliche mentre k6 gira, e stampa il confronto. k6 da solo sa quanto ci mette
// una richiesta, non quanti worker ci sono dall'altra parte.
//
// Ogni iterazione fa i tre passi veri di un caricamento — registra l'immagine,
// spedisce i byte all'object storage con il link firmato, conferma — e una su
// dieci resta ad aspettare che l'elaborazione finisca. Aspettare a ogni
// iterazione bloccherebbe i VU per decine di secondi e falserebbe il carico:
// si campiona, come si fa con le misure che costano.

import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';
import exec from 'k6/execution';

const BASE = __ENV.BASE || 'http://media-platform.test';
// Letta una volta sola, all'avvio: `open` esiste solo in questa fase.
const IMMAGINE = open(__ENV.IMAGE, 'b');

const PASSWORD = 'password-lunga-di-prova';
// Una iterazione su quante aspetta l'esito fino in fondo.
const CAMPIONE = Number(__ENV.SAMPLE || 10);
// Quanto si aspetta al massimo che un'immagine arrivi a DONE.
const ATTESA_MASSIMA_MS = Number(__ENV.MAX_WAIT || 180) * 1000;

// --- le misure che contano ---------------------------------------------------

// Da quando il client comincia a quando l'API dice «preso in carico»: è quello
// che l'utente aspetta davanti allo schermo.
const presa_in_carico = new Trend('presa_in_carico', true);
// Da quando il client comincia a quando l'immagine è pronta: è quello che
// l'autoscaler può accorciare.
const elaborazione_completa = new Trend('elaborazione_completa', true);

const completate = new Counter('immagini_completate');
const fallite = new Counter('immagini_fallite');
const mai_finite = new Counter('immagini_mai_finite');

export const options = {
  scenarios: {
    carico: {
      // A ritmo costante e non «più veloce che puoi»: un utente che carica
      // duecento foto le carica a una certa velocità, non satura la rete. È
      // anche l'unico modo di far crescere la coda in modo prevedibile.
      executor: 'constant-arrival-rate',
      rate: Number(__ENV.RATE || 200),
      timeUnit: '1m',
      duration: __ENV.DURATION || '1m',
      preAllocatedVUs: Number(__ENV.VUS || 30),
      maxVUs: Number(__ENV.MAX_VUS || 150),
    },
  },
  thresholds: {
    // Nessuna immagine persa e nessuna richiesta fallita: sotto carico il
    // sistema può essere lento, non può sbagliare.
    immagini_fallite: ['count==0'],
    http_req_failed: ['rate<0.01'],
  },
  // Le soglie non superate fanno uscire k6 con un codice diverso da zero.
  thresholdsAbortOnFail: false,
};

// --- l'utente, creato una volta sola -----------------------------------------

export function setup() {
  // L'indirizzo lo decide chi lancia il test, non questo script: serve anche
  // a lui, per andare a chiedere al database quanto è durato il lotto. È
  // l'unica misura confrontabile fra due esecuzioni — le latenze campionate
  // qui dipendono da quanto era lunga la coda quando quelle immagini sono
  // state spedite, e fra due giri diversi non si possono mettere a confronto.
  const email = __ENV.EMAIL || `k6-${Date.now()}@example.com`;
  const corpo = JSON.stringify({ email, password: PASSWORD });
  const json = { headers: { 'Content-Type': 'application/json' } };

  // Due sole chiamate di autenticazione in tutto il test, e non è un dettaglio:
  // le rotte di accesso hanno un limite di frequenza, e un test che si
  // registrasse a ogni iterazione si farebbe rifiutare da solo.
  const registrato = http.post(`${BASE}/api/auth/register`, corpo, json);
  check(registrato, { 'utente creato': (r) => r.status === 201 });

  const acceduto = http.post(`${BASE}/api/auth/login`, corpo, json);
  check(acceduto, { 'accesso riuscito': (r) => r.status === 200 });

  return { token: acceduto.json('access_token') };
}

// --- una immagine ------------------------------------------------------------

export default function (dati) {
  const autenticato = {
    headers: {
      Authorization: `Bearer ${dati.token}`,
      'Content-Type': 'application/json',
    },
  };
  const iniziato = Date.now();

  // 1. registra l'immagine e chiedi il link firmato
  const creato = http.post(
    `${BASE}/api/assets`,
    JSON.stringify({
      filename: `carico-${exec.scenario.iterationInTest}.jpg`,
      mime: 'image/jpeg',
      size_bytes: IMMAGINE.byteLength,
    }),
    autenticato,
  );
  if (!check(creato, { 'immagine registrata': (r) => r.status === 201 })) {
    fallite.add(1);
    return;
  }
  const asset_id = creato.json('asset_id');

  // 2. i byte vanno direttamente all'object storage, non passano dall'API
  const caricato = http.put(creato.json('upload.url'), IMMAGINE, {
    headers: { 'Content-Type': 'image/jpeg' },
  });
  if (!check(caricato, { 'file caricato': (r) => r.status === 200 })) {
    fallite.add(1);
    return;
  }

  // 3. conferma: da qui il job è in coda
  const confermato = http.post(`${BASE}/api/assets/${asset_id}/complete`, null, {
    headers: { Authorization: `Bearer ${dati.token}` },
  });
  if (!check(confermato, { 'job accodato': (r) => r.status === 202 })) {
    fallite.add(1);
    return;
  }
  presa_in_carico.add(Date.now() - iniziato);

  // 4. una su dieci resta ad aspettare l'esito
  if (exec.scenario.iterationInTest % CAMPIONE !== 0) {
    return;
  }

  const esito = attendi(dati.token, asset_id, iniziato);
  if (esito === 'DONE') {
    completate.add(1);
    elaborazione_completa.add(Date.now() - iniziato);
  } else if (esito === 'FAILED') {
    fallite.add(1);
  } else {
    mai_finite.add(1);
  }
}

function attendi(token, asset_id, iniziato) {
  const intestazioni = { headers: { Authorization: `Bearer ${token}` } };

  while (Date.now() - iniziato < ATTESA_MASSIMA_MS) {
    const risposta = http.get(
      `${BASE}/api/assets/status?ids=${asset_id}`,
      intestazioni,
    );
    if (risposta.status === 200) {
      const stato = risposta.json('items.0.status');
      if (stato === 'DONE' || stato === 'FAILED') {
        return stato;
      }
    }
    // Lo stesso intervallo del frontend: si misura quello che vede l'utente.
    sleep(2);
  }
  return 'TIMEOUT';
}
