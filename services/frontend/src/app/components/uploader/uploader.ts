import { Component, DestroyRef, computed, inject, output, signal } from '@angular/core';

import {
  ACCEPTED_TYPES,
  MAX_UPLOAD_BYTES,
  UploadItem,
  UploadService,
} from '../../core/upload.service';

/**
 * L'area di caricamento: si lasciano cadere i file, oppure si scelgono.
 *
 * Qui niente aspetta che l'elaborazione finisca. Mandare i byte e confermare il
 * caricamento è tutto il mestiere di questo componente; quello che viene dopo
 * appartiene al worker, e lo mostrerà la libreria.
 */
@Component({
  selector: 'app-uploader',
  templateUrl: './uploader.html',
  styleUrl: './uploader.css',
})
export class UploaderComponent {
  /** Emesso quando un lotto è finito, così la libreria può ricaricarsi. */
  readonly finished = output<void>();

  protected readonly accept = ACCEPTED_TYPES.join(',');
  protected readonly maxMegabytes = MAX_UPLOAD_BYTES / (1024 * 1024);

  protected readonly items = signal<UploadItem[]>([]);
  protected readonly dragging = signal(false);

  protected readonly counts = computed(() => {
    const items = this.items();
    let done = 0;
    let failed = 0;
    for (const item of items) {
      if (item.phase() === 'done') {
        done += 1;
      } else if (item.phase() === 'error') {
        failed += 1;
      }
    }
    return { total: items.length, done, failed, running: items.length - done - failed };
  });

  private readonly service = inject(UploadService);

  /** Quanto è annidato il trascinamento, così passare su un figlio non spegne l'evidenziazione. */
  private depth = 0;

  constructor() {
    // Senza questo, un file lasciato cadere qualche pixel fuori dalla zona fa
    // aprire l'immagine al browser e sostituire la pagina, perdendo tutta la coda.
    const swallow = (event: DragEvent) => event.preventDefault();
    document.addEventListener('dragover', swallow);
    document.addEventListener('drop', swallow);
    inject(DestroyRef).onDestroy(() => {
      document.removeEventListener('dragover', swallow);
      document.removeEventListener('drop', swallow);
    });
  }

  protected onSelect(event: Event): void {
    const input = event.target as HTMLInputElement;
    this.add(input.files);
    // Svuotarlo fa sì che scegliere due volte lo stesso file riemetta l'evento.
    input.value = '';
  }

  protected onDragEnter(event: DragEvent): void {
    event.preventDefault();
    this.depth += 1;
    this.dragging.set(true);
  }

  /** Obbligatorio: senza, il browser rifiuta il rilascio e apre il file. */
  protected onDragOver(event: DragEvent): void {
    event.preventDefault();
  }

  protected onDragLeave(): void {
    this.depth -= 1;
    if (this.depth <= 0) {
      this.depth = 0;
      this.dragging.set(false);
    }
  }

  protected onDrop(event: DragEvent): void {
    event.preventDefault();
    this.depth = 0;
    this.dragging.set(false);
    this.add(event.dataTransfer?.files ?? null);
  }

  protected retry(item: UploadItem): void {
    this.start([item]);
  }

  /** Toglie dallo schermo le righe finite; quelle in corso restano. */
  protected clear(): void {
    this.items.update((current) => current.filter((item) => !item.finished));
  }

  protected marker(item: UploadItem): string {
    switch (item.phase()) {
      case 'done':
        return '□';
      case 'error':
        return '■';
      case 'queued':
        return '▫';
      default:
        return '▪';
    }
  }

  protected wording(item: UploadItem): string {
    switch (item.phase()) {
      case 'queued':
        return 'In coda';
      case 'registering':
        return 'Registrazione';
      case 'sending':
        return `Invio ${item.progress()}%`;
      case 'confirming':
        return 'Conferma';
      case 'done':
        return 'Inviata';
      default:
        return 'Errore';
    }
  }

  protected size(bytes: number): string {
    if (bytes >= 1024 * 1024) {
      return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
    }
    return `${Math.round(bytes / 1024)} kB`;
  }

  private add(files: FileList | null): void {
    const chosen = Array.from(files ?? []);
    if (chosen.length === 0) {
      return;
    }
    const items = chosen.map((file) => new UploadItem(file));
    this.items.update((current) => [...current, ...items]);
    this.start(items);
  }

  private start(items: UploadItem[]): void {
    this.service.run(items).subscribe({
      // La libreria si ricarica una volta per lotto, non una per file: cento
      // immagini significherebbero altrimenti cento interrogazioni.
      complete: () => this.finished.emit(),
    });
  }
}
