import { HttpClient, HttpEventType } from '@angular/common/http';
import { Injectable, inject, signal } from '@angular/core';
import { Observable, catchError, filter, from, map, mergeMap, of, switchMap, tap } from 'rxjs';

import { messageOf } from './errors';
import { CreatedAsset, UploadTarget } from './models';

/** Formati che l'api-service accetta, ripetuti qui per rifiutare prima. */
export const ACCEPTED_TYPES = ['image/jpeg', 'image/png', 'image/webp'] as const;

/** Lo stesso limite di MAX_UPLOAD_BYTES sull'api-service, che ha l'ultima parola. */
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024;

/**
 * File spediti nello stesso momento.
 *
 * Il carico è a raffiche per natura — qualcuno lascia cadere duecento foto in
 * una volta — e duecento richieste simultanee affamerebbero la riserva di
 * connessioni del browser rendendo più lento ogni singolo caricamento. Tre alla
 * volta tengono il collegamento occupato lasciando spazio alle chiamate alla nostra API.
 */
const CONCURRENCY = 3;

export type UploadPhase =
  | 'queued'
  | 'registering'
  | 'sending'
  | 'confirming'
  | 'done'
  | 'error';

/**
 * Identificativi per l'elenco a schermo.
 *
 * Deliberatamente non crypto.randomUUID(): quella funzione esiste solo in un
 * contesto sicuro, e il cluster si raggiunge in http semplice su un indirizzo
 * IP, dove è indefinita. Un contatore non costa niente e funziona sempre.
 */
let sequence = 0;

/**
 * Un file mentre sale.
 *
 * Ogni voce porta i propri segnali, così un evento di avanzamento ridisegna
 * quella singola riga invece di ricostruire un elenco che può averne centinaia.
 */
export class UploadItem {
  readonly id = `upload-${++sequence}`;
  readonly phase = signal<UploadPhase>('queued');
  readonly progress = signal(0);
  readonly error = signal<string | null>(null);

  assetId: string | null = null;

  constructor(readonly file: File) {}

  get finished(): boolean {
    return this.phase() === 'done' || this.phase() === 'error';
  }

  fail(reason: string): void {
    this.error.set(reason);
    this.phase.set('error');
  }
}

/**
 * I tre passi di un caricamento.
 *
 * 1. l'api-service registra l'immagine e restituisce un link firmato;
 * 2. il browser manda i byte direttamente all'object storage;
 * 3. all'api-service viene detto che è fatta, e accoda il lavoro di elaborazione.
 *
 * Il file non passa mai dai nostri servizi: loro firmano e registrano, basta.
 */
@Injectable({ providedIn: 'root' })
export class UploadService {
  private readonly http = inject(HttpClient);

  /** Esegue l'intero lotto, pochi file alla volta. */
  run(items: readonly UploadItem[]): Observable<UploadItem> {
    return from(items).pipe(mergeMap((item) => this.one(item), CONCURRENCY));
  }

  /**
   * Un file solo, dalla registrazione alla conferma.
   *
   * Non fallisce mai: un file rifiutato registra il proprio motivo e il lotto
   * prosegue. Una foto illeggibile su duecento non deve fermare le altre
   * centonovantanove.
   */
  private one(item: UploadItem): Observable<UploadItem> {
    const refusal = refusalFor(item.file);
    if (refusal !== null) {
      item.fail(refusal);
      return of(item);
    }

    item.phase.set('registering');
    item.error.set(null);
    item.progress.set(0);

    return this.http
      .post<CreatedAsset>('/api/assets', {
        filename: item.file.name,
        mime: item.file.type,
        size_bytes: item.file.size,
      })
      .pipe(
        switchMap((created) => {
          item.assetId = created.asset_id;
          item.phase.set('sending');
          return this.send(created.upload, item).pipe(
            switchMap(() => {
              item.phase.set('confirming');
              return this.http.post(`/api/assets/${created.asset_id}/complete`, {});
            }),
          );
        }),
        map(() => {
          item.progress.set(100);
          item.phase.set('done');
          return item;
        }),
        catchError((failure: unknown) => {
          item.fail(messageOf(failure));
          return of(item);
        }),
      );
  }

  /**
   * I byte, direttamente all'object storage.
   *
   * Il link firmato porta già con sé le proprie credenziali nella stringa di
   * interrogazione, e il tipo di contenuto fa parte di ciò che la firma copre: va
   * mandato esattamente come l'ha dichiarato l'api-service. Emette una volta, quando l'archivio risponde.
   */
  private send(target: UploadTarget, item: UploadItem): Observable<unknown> {
    return this.http
      .put(target.url, item.file, {
        headers: target.headers,
        reportProgress: true,
        observe: 'events',
      })
      .pipe(
        tap((event) => {
          if (event.type === HttpEventType.UploadProgress && event.total) {
            item.progress.set(Math.round((event.loaded / event.total) * 100));
          }
        }),
        filter((event) => event.type === HttpEventType.Response),
      );
  }
}

/** Perché questo file non si può spedire, o null quando si può. */
export function refusalFor(file: File): string | null {
  if (!(ACCEPTED_TYPES as readonly string[]).includes(file.type)) {
    return 'Formato non supportato: sono ammessi JPEG, PNG e WebP.';
  }
  if (file.size === 0) {
    return 'Il file è vuoto.';
  }
  if (file.size > MAX_UPLOAD_BYTES) {
    return `Il file supera il limite di ${MAX_UPLOAD_BYTES / (1024 * 1024)} MB.`;
  }
  return null;
}
