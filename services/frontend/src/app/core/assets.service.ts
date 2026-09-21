import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';

import { Asset, AssetPage, AssetStatus } from './models';

/** Quello che restituisce l'endpoint degli stati: solo ciò che cambia davvero. */
export interface AssetState {
  id: string;
  status: AssetStatus;
  error_message: string | null;
  updated_at: string;
}

/**
 * I filtri che la libreria capisce.
 *
 * I numeri viaggiano come testo perché è ciò che contiene un campo di un modulo
 * ed è ciò che porta un indirizzo: l'api-service li analizza e li convalida, ed
 * è comunque lui a dover rifiutare un valore malformato.
 */
export interface LibraryQuery {
  page?: number;
  page_size?: number;
  status?: string;
  filename?: string;
  from?: string;
  to?: string;
  camera?: string;
  lens?: string;
  focal_length?: string;
  iso_min?: string;
  iso_max?: string;
}

@Injectable({ providedIn: 'root' })
export class AssetsService {
  private readonly http = inject(HttpClient);

  list(query: LibraryQuery = {}): Observable<AssetPage> {
    let params = new HttpParams();
    for (const [key, value] of Object.entries(query)) {
      // Un filtro vuoto è un filtro assente: mandarlo chiederebbe all'API di
      // confrontarsi con la stringa vuota.
      if (value !== undefined && value !== null && value !== '') {
        params = params.set(key, String(value));
      }
    }
    return this.http.get<AssetPage>('/api/assets', { params });
  }

  /** Un'immagine con le sue varianti, un link all'originale e i suoi dati EXIF. */
  get(id: string): Observable<Asset> {
    return this.http.get<Asset>(`/api/assets/${id}`);
  }

  /**
   * Gli stati delle immagini sorvegliate, e nient'altro.
   *
   * Deliberatamente non un ricaricamento completo della libreria: quello
   * rifirmerebbe ogni link di scaricamento a ogni giro, e poiché una firma nuova è
   * un indirizzo diverso, il browser riscaricherebbe ogni miniatura due secondi
   * dopo l'ultima volta.
   */
  states(ids: readonly string[]): Observable<{ items: AssetState[] }> {
    const params = new HttpParams().set('ids', ids.join(','));
    return this.http.get<{ items: AssetState[] }>('/api/assets/status', { params });
  }

  /** Rimette in coda un'immagine fallita — o piantata. */
  retry(id: string): Observable<unknown> {
    return this.http.post(`/api/assets/${id}/retry`, {});
  }

  /** Rimuove l'immagine, le sue varianti e i suoi metadati tecnici. */
  delete(id: string): Observable<unknown> {
    return this.http.delete(`/api/assets/${id}`);
  }
}
