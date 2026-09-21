/** Le forme restituite dall'api-service. */

export type AssetStatus = 'PENDING' | 'PROCESSING' | 'DONE' | 'FAILED';

export interface User {
  id: string;
  email: string;
  created_at: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
}

/** Dove e come mandare il file, direttamente all'object storage. */
export interface UploadTarget {
  url: string;
  method: string;
  headers: Record<string, string>;
  expires_in: number;
}

export interface CreatedAsset {
  asset_id: string;
  status: AssetStatus;
  created_at: string;
  upload: UploadTarget;
}

export interface Variant {
  kind: 'thumb' | 'medium' | 'large';
  width: number;
  height: number;
  size_bytes: number;
  /** Link firmato che mostra l'immagine: lo usano la griglia e l'anteprima. */
  url: string;
  /** Link firmato che la salva con un nome leggibile. Solo la pagina di dettaglio. */
  download_url?: string;
}

export interface Asset {
  id: string;
  filename: string;
  mime: string;
  size_bytes: number | null;
  status: AssetStatus;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  variants: Variant[];
  original_url?: string;
  original_download_url?: string;
  metadata?: TechnicalMetadata | null;
}

export interface TechnicalMetadata {
  exif: Record<string, string | number>;
  dimensions: { width: number; height: number };
  checksum: string | null;
}

export interface AssetPage {
  items: Asset[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
  truncated?: boolean;
}

/** Un messaggio scritto dal notification-service. */
export interface Notification {
  id: string;
  asset_id: string;
  event: 'asset.processed' | 'asset.failed';
  subject: string;
  body: string;
  filename: string;
  asset_status: AssetStatus;
  /** Quando è stato consegnato al suo canale — in locale, il registro. */
  sent_at: string | null;
  created_at: string;
}

export interface NotificationPage {
  items: Notification[];
  page: number;
  page_size: number;
  total: number;
  pages: number;
}

/** L'api-service risponde in JSON anche per gli errori, sempre in questa forma. */
export interface ApiError {
  error: { code: string; message: string };
}
