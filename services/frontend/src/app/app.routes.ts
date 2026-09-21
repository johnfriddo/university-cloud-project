import { Routes } from '@angular/router';

import { authGuard, guestGuard } from './core/auth.guard';

export const routes: Routes = [
  { path: '', pathMatch: 'full', redirectTo: 'libreria' },
  {
    path: 'accedi',
    canActivate: [guestGuard],
    // Caricata alla bisogna: una volta entrati, la pagina di accesso non viene più scaricata.
    loadComponent: () => import('./pages/login/login').then((m) => m.LoginPage),
  },
  {
    path: 'libreria',
    canActivate: [authGuard],
    loadComponent: () => import('./pages/library/library').then((m) => m.LibraryPage),
  },
  {
    path: 'avvisi',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/notifications/notifications').then((m) => m.NotificationsPage),
  },
  {
    path: 'immagini/:id',
    canActivate: [authGuard],
    loadComponent: () =>
      import('./pages/asset-detail/asset-detail').then((m) => m.AssetDetailPage),
  },
  { path: '**', redirectTo: 'libreria' },
];
