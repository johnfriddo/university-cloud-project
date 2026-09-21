import { DatePipe, Location, formatDate } from '@angular/common';
import { Component, computed, effect, inject, input, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';

import { AssetsService } from '../../core/assets.service';
import { messageOf } from '../../core/errors';
import { Asset, Variant } from '../../core/models';

/** I campi EXIF, in ordine di lettura, con le parole mostrate all'utente. */
const EXIF_FIELDS: ReadonlyArray<readonly [string, string]> = [
  ['make', 'Marca'],
  ['model', 'Modello'],
  ['lens', 'Obiettivo'],
  ['focal_length', 'Focale'],
  ['aperture', 'Diaframma'],
  ['exposure', 'Tempo di posa'],
  ['iso', 'ISO'],
  ['taken_at', 'Scattata il'],
];

/** Dalla più grande alla più piccola: l'ordine in cui si elencano le varianti. */
const VARIANT_ORDER: ReadonlyArray<Variant['kind']> = ['large', 'medium', 'thumb'];

const VARIANT_LABELS: Record<Variant['kind'], string> = {
  large: 'Grande, con filigrana',
  medium: 'Media',
  thumb: 'Miniatura',
};

/**
 * Un'immagine: l'anteprima, le tre varianti e i dati tecnici.
 *
 * È l'unica pagina che legge il documentale, attraverso
 * GET /api/assets/{id}: i dati EXIF di una fotografia hanno una forma diversa
 * per ogni fotocamera, ed è esattamente il motivo per cui non stanno nel relazionale.
 */
@Component({
  selector: 'app-asset-detail',
  imports: [DatePipe, RouterLink],
  templateUrl: './asset-detail.html',
  styleUrl: './asset-detail.css',
})
export class AssetDetailPage {
  /** Legato alla rotta: /immagini/:id */
  readonly id = input.required<string>();

  protected readonly asset = signal<Asset | null>(null);
  protected readonly loading = signal(true);
  protected readonly error = signal<string | null>(null);
  protected readonly retrying = signal(false);
  protected readonly deleting = signal(false);

  /** La variante più grande che c'è, o l'originale quando non ce n'è ancora nessuna. */
  protected readonly preview = computed(() => {
    const asset = this.asset();
    if (asset === null) {
      return null;
    }
    for (const kind of VARIANT_ORDER) {
      const variant = asset.variants.find((candidate) => candidate.kind === kind);
      if (variant !== undefined) {
        return variant.url;
      }
    }
    // Dopo un fallimento l'originale è esattamente il file che nessuno è riuscito a
    // leggere: mostrarlo produrrebbe un'immagine rotta e nessuna informazione.
    return asset.status === 'FAILED' ? null : (asset.original_url ?? null);
  });

  protected readonly variants = computed(() => {
    const asset = this.asset();
    if (asset === null) {
      return [];
    }
    // L'API ordina per nome, il che metterebbe «large» prima di «medium» prima di
    // «thumb» per nessuna ragione che un lettore riconosca.
    return VARIANT_ORDER.map((kind) => asset.variants.find((v) => v.kind === kind)).filter(
      (variant): variant is Variant => variant !== undefined,
    );
  });

  protected readonly exif = computed(() => {
    const values = this.asset()?.metadata?.exif;
    if (values === undefined || values === null) {
      return [];
    }

    const rows: { label: string; value: string }[] = [];
    for (const [key, label] of EXIF_FIELDS) {
      const value = values[key];
      if (value === undefined || value === null || value === '') {
        continue;
      }
      rows.push({ label, value: key === 'taken_at' ? asDate(value) : String(value) });
    }
    return rows;
  });

  private readonly service = inject(AssetsService);
  private readonly location = inject(Location);
  private readonly router = inject(Router);

  constructor() {
    effect(() => this.load(this.id()));
  }

  /**
   * Torna da dove l'utente è venuto.
   *
   * Un semplice link alla libreria perderebbe i filtri e la pagina che stava
   * guardando. Tornare indietro nella cronologia li conserva, perché stanno
   * nell'indirizzo. Aprendola da un preferito non c'è cronologia a cui tornare, e
   * la libreria è la destinazione sensata.
   */
  protected back(): void {
    if (window.history.length > 1) {
      this.location.back();
    } else {
      void this.router.navigate(['/libreria']);
    }
  }

  protected retry(): void {
    const asset = this.asset();
    if (asset === null || this.retrying()) {
      return;
    }
    this.retrying.set(true);
    this.error.set(null);

    this.service.retry(asset.id).subscribe({
      next: () => {
        this.retrying.set(false);
        this.load(asset.id);
      },
      error: (failure: unknown) => {
        this.retrying.set(false);
        this.error.set(messageOf(failure));
      },
    });
  }

  /**
   * Chiede una conferma, poi rimuove l'immagine per sempre.
   *
   * La conferma non è un ornamento: qui niente si può annullare, e se ne va
   * anche il file originale. Due clic sono la protezione più economica che
   * esista contro un clic sbagliato.
   */
  protected remove(): void {
    const asset = this.asset();
    if (asset === null || this.deleting()) {
      return;
    }
    if (!confirm(`Eliminare «${asset.filename}»? L'originale e le varianti non si recuperano.`)) {
      return;
    }

    this.deleting.set(true);
    this.error.set(null);

    this.service.delete(asset.id).subscribe({
      // Non c'è più niente da mostrare in questa pagina: si torna alla libreria.
      next: () => void this.router.navigate(['/libreria']),
      error: (failure: unknown) => {
        this.deleting.set(false);
        this.error.set(messageOf(failure));
      },
    });
  }

  protected labelOf(kind: Variant['kind']): string {
    return VARIANT_LABELS[kind];
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

  protected size(bytes: number | null): string {
    if (bytes === null) {
      return '—';
    }
    if (bytes >= 1024 * 1024) {
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    if (bytes >= 1024) {
      return `${Math.round(bytes / 1024)} kB`;
    }
    return `${bytes} byte`;
  }

  private load(id: string): void {
    this.loading.set(true);
    this.error.set(null);

    this.service.get(id).subscribe({
      next: (asset) => {
        this.asset.set(asset);
        this.loading.set(false);
      },
      error: (failure: unknown) => {
        this.error.set(messageOf(failure));
        this.loading.set(false);
      },
    });
  }
}

/** La data della fotocamera, già normalizzata in ISO 8601 dal worker. */
function asDate(value: string | number): string {
  try {
    return formatDate(String(value), 'dd/MM/yyyy HH:mm', 'en-US');
  } catch {
    // Una data illeggibile vale comunque la pena di essere mostrata come l'ha scritta la fotocamera.
    return String(value);
  }
}
