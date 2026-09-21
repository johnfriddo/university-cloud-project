import { DatePipe } from '@angular/common';
import { Component, DestroyRef, computed, inject, signal } from '@angular/core';
import { takeUntilDestroyed } from '@angular/core/rxjs-interop';
import { FormBuilder, ReactiveFormsModule } from '@angular/forms';
import { ActivatedRoute, Params, Router, RouterLink } from '@angular/router';
import { EMPTY, Subscription, filter, switchMap, timer } from 'rxjs';

import { UploaderComponent } from '../../components/uploader/uploader';
import { AssetState, AssetsService, LibraryQuery } from '../../core/assets.service';
import { messageOf } from '../../core/errors';
import { Asset } from '../../core/models';
import { NotificationsService } from '../../core/notifications.service';

/** Ogni quanto si rileggono gli stati. */
const POLL_INTERVAL_MS = 2000;

/**
 * Giri senza un solo cambiamento prima che la sorveglianza si arrenda — cinque minuti.
 *
 * Un'immagine può restare non terminale per sempre: un lavoro morto mentre il
 * database era irraggiungibile non lascia nessuno in grado di registrare il
 * fallimento, e nessun messaggio nel broker. Senza questo limite una riga così
 * terrebbe la pagina a interrogare l'API ogni due secondi finché la scheda resta aperta.
 */
const MAX_IDLE_TICKS = 150;

const PAGE_SIZE = 24;

/** I filtri, nell'ordine in cui compaiono sullo schermo. */
const FILTER_KEYS = [
  'filename',
  'status',
  'from',
  'to',
  'camera',
  'lens',
  'focal_length',
  'iso_min',
  'iso_max',
] as const;

/** Quelli a cui l'api-service risponde interrogando il documentale. */
const TECHNICAL_KEYS = ['camera', 'lens', 'focal_length', 'iso_min', 'iso_max'] as const;

/**
 * La libreria: la griglia, i filtri, la paginazione.
 *
 * I filtri vivono nella barra degli indirizzi invece che dentro il componente.
 * Una vista filtrata diventa così un link che si può condividere o mettere fra i
 * preferiti, il pulsante «indietro» si comporta come l'utente si aspetta, e
 * ricaricare la pagina conserva quello che c'era. In più lascia una sola via
 * verso i dati: cambia l'indirizzo, e la pagina lo legge.
 */
@Component({
  selector: 'app-library',
  imports: [DatePipe, ReactiveFormsModule, RouterLink, UploaderComponent],
  templateUrl: './library.html',
  styleUrl: './library.css',
})
export class LibraryPage {
  protected readonly assets = signal<Asset[]>([]);
  protected readonly total = signal(0);
  protected readonly page = signal(1);
  protected readonly pages = signal(1);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);

  /** La ricerca tecnica ha toccato il tetto: la risposta è parziale, e lo dice. */
  protected readonly truncated = signal(false);

  /** Apre il pannello tecnico quando si arriva da un link che lo usa. */
  protected readonly technicalOpen = signal(false);

  /** Le immagini il cui nuovo tentativo è in volo, così il pulsante non si può premere due volte. */
  protected readonly retrying = signal<ReadonlySet<string>>(new Set());

  /** La sorveglianza si è arresa: non si muove niente da molto tempo. */
  protected readonly stalled = signal(false);

  protected readonly working = computed(() => this.pendingIn(this.assets()).length);
  protected readonly filtered = computed(() => this.page() > 1 || this.activeFilters() > 0);

  protected readonly form = inject(FormBuilder).nonNullable.group({
    filename: '',
    status: '',
    from: '',
    to: '',
    camera: '',
    lens: '',
    focal_length: '',
    iso_min: '',
    iso_max: '',
  });

  private readonly service = inject(AssetsService);
  private readonly notifications = inject(NotificationsService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  private readonly activeFilters = signal(0);

  /**
   * La miniatura mostrata per ogni immagine.
   *
   * I link firmati sono temporanei, quindi ogni ricaricamento produce un indirizzo
   * diverso per la stessa immagine — e un indirizzo diverso significa che il
   * browser la riscarica. Mentre la pagina interroga, si conserva l'indirizzo già
   * a schermo: solo una navigazione vera (un filtro, una pagina, un ricaricamento a mano) ne chiede di nuovi.
   */
  private readonly thumbs = new Map<string, string>();

  private watcher: Subscription | null = null;
  private idleTicks = 0;

  constructor() {
    // Anche lasciare la pagina deve fermare l'orologio.
    inject(DestroyRef).onDestroy(() => this.stopWatching());

    // Una via sola: cambia l'indirizzo, la pagina lo legge e carica. Premere
    // «Filtra» naviga, non interroga.
    this.route.queryParamMap.pipe(takeUntilDestroyed()).subscribe((params) => {
      const values: Record<string, string> = {};
      let active = 0;
      for (const key of FILTER_KEYS) {
        const value = params.get(key) ?? '';
        values[key] = value;
        if (value !== '') {
          active += 1;
        }
      }

      this.form.patchValue(values, { emitEvent: false });
      this.activeFilters.set(active);
      this.technicalOpen.set(TECHNICAL_KEYS.some((key) => values[key] !== ''));
      this.page.set(Math.max(1, Number(params.get('page')) || 1));
      this.load();
    });
  }

  /** Applica quello che c'è nel modulo: pagina uno, perché i risultati sono nuovi. */
  protected applyFilters(): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.queryParams(1),
    });
  }

  protected reset(): void {
    this.form.reset();
    void this.router.navigate([], { relativeTo: this.route, queryParams: {} });
  }

  protected goTo(page: number): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: this.queryParams(page),
    });
  }

  /**
   * Rilegge la pagina dopo un caricamento o un completamento.
   *
   * Senza il segnale di «sto caricando»: la griglia a schermo è ancora valida, e
   * svuotarla per la durata di un'interrogazione sembrerebbe che le immagini siano sparite.
   */
  protected reload(): void {
    this.load(true);
  }

  protected load(silent = false): void {
    this.loading.set(!silent);
    this.error.set(null);

    this.service.list(this.currentQuery()).subscribe({
      next: (page) => {
        // Un numero di pagina scritto nell'indirizzo, o lasciato lì da un filtro che
        // adesso trova meno risultati: si va all'ultima pagina vera invece di mostrare
        // una griglia vuota sotto «Pagina 4000 di 2».
        if (page.total > 0 && page.page > page.pages) {
          this.goTo(page.pages);
          return;
        }

        this.remember(page.items, silent);
        this.assets.set(page.items);
        this.total.set(page.total);
        this.pages.set(Math.max(1, page.pages));
        this.truncated.set(page.truncated === true);
        this.loading.set(false);
        this.watch();
      },
      error: (failure: unknown) => {
        this.error.set(messageOf(failure));
        this.loading.set(false);
      },
    });
  }

  protected retry(asset: Asset): void {
    if (this.retrying().has(asset.id)) {
      return;
    }
    this.mark(asset.id, true);
    this.error.set(null);

    this.service.retry(asset.id).subscribe({
      next: () => {
        this.mark(asset.id, false);
        // Mostra subito il nuovo stato invece di aspettare il giro successivo.
        this.patch([
          {
            id: asset.id,
            status: 'PENDING',
            error_message: null,
            updated_at: new Date().toISOString(),
          },
        ]);
        this.watch();
      },
      error: (failure: unknown) => {
        this.mark(asset.id, false);
        this.error.set(messageOf(failure));
        // Il rifiuto di solito significa che l'immagine si è mossa da sola: la si rilegge.
        this.reload();
      },
    });
  }

  protected thumb(asset: Asset): string | null {
    return this.thumbs.get(asset.id) ?? null;
  }

  /** Quadrato vuoto quando è pronta, pieno quando è fallita, piccolo mentre lavora. */
  protected marker(asset: Asset): string {
    switch (asset.status) {
      case 'DONE':
        return '□';
      case 'FAILED':
        return '■';
      default:
        return '▪';
    }
  }

  protected wording(asset: Asset): string {
    switch (asset.status) {
      case 'DONE':
        return 'Pronta';
      case 'FAILED':
        return 'Errore';
      case 'PROCESSING':
        return 'In lavorazione';
      default:
        return 'In attesa';
    }
  }

  /**
   * Quando ha senso offrire un altro tentativo.
   *
   * Sempre dopo un fallimento. Anche su un'immagine su cui la sorveglianza si è
   * arresa, che è l'unica via d'uscita per un lavoro che nessuno ha potuto marcare
   * come fallito. Se sia davvero troppo presto lo decide l'api-service, che rifiuta con un motivo.
   */
  protected canRetry(asset: Asset): boolean {
    return asset.status === 'FAILED' || (this.stalled() && asset.status !== 'DONE');
  }

  private currentQuery(): LibraryQuery {
    return { ...this.form.getRawValue(), page: this.page(), page_size: PAGE_SIZE };
  }

  /** La barra degli indirizzi porta i filtri impostati, e nient'altro. */
  private queryParams(page: number): Params {
    const raw = this.form.getRawValue();
    const params: Params = {};
    for (const [key, value] of Object.entries(raw)) {
      if (value !== '') {
        params[key] = value;
      }
    }
    if (page > 1) {
      params['page'] = page;
    }
    return params;
  }

  private remember(assets: readonly Asset[], keepExisting: boolean): void {
    if (!keepExisting) {
      this.thumbs.clear();
    }
    for (const asset of assets) {
      if (this.thumbs.has(asset.id)) {
        continue;
      }
      const thumb = asset.variants.find((variant) => variant.kind === 'thumb');
      if (thumb !== undefined) {
        this.thumbs.set(asset.id, thumb.url);
      }
    }
  }

  /**
   * Comincia — o ricomincia — a sorvegliare tutto ciò che non è finito.
   *
   * Un sorvegliante alla volta, e nessuno quando non c'è niente da sorvegliare:
   * una libreria di immagini tutte pronte non deve tenere in piedi una richiesta
   * ogni due secondi finché la scheda resta aperta.
   */
  private watch(): void {
    this.stopWatching();

    if (this.working() === 0) {
      return;
    }

    this.idleTicks = 0;
    this.stalled.set(false);

    this.watcher = timer(POLL_INTERVAL_MS, POLL_INTERVAL_MS)
      .pipe(
        // Una scheda in secondo piano non la guarda nessuno: saltare quei giri
        // risparmia all'API una richiesta ogni due secondi per scheda dimenticata.
        filter(() => document.visibilityState === 'visible'),
        switchMap(() => {
          const ids = this.pendingIn(this.assets());
          // Chiedere con un elenco vuoto sarebbe una richiesta sbagliata, non una
          // risposta vuota: semplicemente non è rimasto niente da chiedere.
          return ids.length > 0 ? this.service.states(ids) : EMPTY;
        }),
      )
      .subscribe({
        next: (page) => this.apply(page.items),
        // Un giro fallito non merita un allarme: ci si ferma, e la prossima azione
        // — un caricamento, un ricaricamento — fa ripartire le cose.
        error: () => this.stopWatching(),
      });
  }

  private apply(states: AssetState[]): void {
    if (this.patch(states)) {
      // Un'immagine finita ha anche varianti e link, che l'endpoint degli stati non
      // porta. Il ricaricamento li porta, e rimette la sorveglianza su ciò che ancora
      // lavora: finché la sua risposta non arriva non è rimasto niente di utile da
      // chiedere.
      this.stopWatching();
      this.reload();
      // Qualcosa è finito, quindi per quel qualcosa è stato scritto un avviso: il
      // contrassegno nell'intestazione resterebbe altrimenti indietro fino al prossimo caricamento della pagina.
      this.notifications.refresh();
      return;
    }

    if (this.working() === 0) {
      this.stopWatching();
      return;
    }

    this.idleTicks += 1;
    if (this.idleTicks >= MAX_IDLE_TICKS) {
      this.stopWatching();
      this.stalled.set(true);
    }
  }

  /** Scrive dentro i nuovi stati, e dice se qualcosa si è davvero mosso. */
  private patch(states: readonly AssetState[]): boolean {
    const byId = new Map(states.map((state) => [state.id, state]));
    let changed = false;

    this.assets.update((current) =>
      current.map((asset) => {
        const state = byId.get(asset.id);
        if (state === undefined || state.status === asset.status) {
          return asset;
        }
        changed = true;
        return {
          ...asset,
          status: state.status,
          error_message: state.error_message,
          updated_at: state.updated_at,
        };
      }),
    );
    return changed;
  }

  private pendingIn(assets: readonly Asset[]): string[] {
    return assets
      .filter((asset) => asset.status === 'PENDING' || asset.status === 'PROCESSING')
      .map((asset) => asset.id);
  }

  private mark(id: string, busy: boolean): void {
    this.retrying.update((current) => {
      const next = new Set(current);
      if (busy) {
        next.add(id);
      } else {
        next.delete(id);
      }
      return next;
    });
  }

  private stopWatching(): void {
    this.watcher?.unsubscribe();
    this.watcher = null;
  }
}
