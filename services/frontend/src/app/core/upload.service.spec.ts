import { MAX_UPLOAD_BYTES, refusalFor } from './upload.service';

/**
 * I controlli fatti nel browser prima ancora di chiamare l'API.
 *
 * L'ultima parola ce l'ha l'api-service, ma rifiutare qui risparmia un viaggio
 * e dà subito all'utente il motivo, sulla riga giusta dell'elenco.
 */
function file(name: string, type: string, size = 1024): File {
  const made = new File(['x'], name, { type });
  // Costruire davvero un file da 26 MB solo per provare un limite sarebbe uno
  // spreco: il controllo legge soltanto la dimensione.
  Object.defineProperty(made, 'size', { value: size });
  return made;
}

describe('refusalFor', () => {
  it.each([
    ['foto.jpg', 'image/jpeg'],
    ['foto.png', 'image/png'],
    ['foto.webp', 'image/webp'],
  ])('accetta %s', (name, type) => {
    expect(refusalFor(file(name, type))).toBeNull();
  });

  it('rifiuta un formato non ammesso, spiegando quali lo sono', () => {
    const reason = refusalFor(file('disegno.svg', 'image/svg+xml'));

    expect(reason).toContain('JPEG, PNG e WebP');
  });

  it('rifiuta un file di cui il browser non conosce il tipo', () => {
    // Il tipo lo deduce il browser dall'estensione: un nome senza estensione
    // arriva con il tipo vuoto.
    expect(refusalFor(file('senza-estensione', ''))).not.toBeNull();
  });

  it('rifiuta un file vuoto', () => {
    expect(refusalFor(file('vuoto.jpg', 'image/jpeg', 0))).toBe('Il file è vuoto.');
  });

  it('accetta un file esattamente al limite', () => {
    expect(refusalFor(file('giusto.jpg', 'image/jpeg', MAX_UPLOAD_BYTES))).toBeNull();
  });

  it('rifiuta un file oltre il limite, dicendo quale', () => {
    const reason = refusalFor(file('enorme.jpg', 'image/jpeg', MAX_UPLOAD_BYTES + 1));

    expect(reason).toContain('25 MB');
  });
});
