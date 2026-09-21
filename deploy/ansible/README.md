# Ansible — la configurazione delle macchine

Ansible fa una cosa sola in questo progetto: **prende tre macchine Ubuntu vuote
e le trasforma in un cluster Kubernetes**. Non installa l'applicazione — quella
è competenza di Terraform e Helm, e la linea fra i due è netta:

> Ansible tocca le macchine, Terraform tocca il cluster.

## Comandi

```bash
cd deploy/ansible

ansible-playbook ping.yml                 # il canale funziona?
ansible-playbook site.yml                 # tutto
ansible-playbook site.yml --tags node     # solo la preparazione dei nodi
ansible-playbook site.yml --diff          # mostra cosa cambia, riga per riga
```

I comandi si danno **da questa cartella**: è qui che sta `ansible.cfg`, e i
percorsi dentro sono relativi a essa.

## La prova che conta

Rilanciare `site.yml` su un cluster già in piedi non deve cambiare niente:

```
media-cp   : ok=39   changed=0   failed=0
media-w1   : ok=22   changed=0   failed=0
media-w2   : ok=22   changed=0   failed=0
```

**`changed=0` è il punto.** È la differenza fra uno script e l'automazione
dichiarativa: lo script rifà tutto da capo ogni volta — riscrive i file,
riavvia containerd due volte — mentre qui ogni passo prima guarda com'è la
macchina e agisce solo se serve. Su un cluster in esercizio la differenza non è
estetica: un riavvio inutile di containerd riavvia tutti i pod di quel nodo.

## L'inventario è uno script

`inventory/multipass.py` chiede a Multipass quali macchine esistono e dove
sono. Un elenco scritto a mano conterrebbe tre indirizzi che Multipass
riassegna a ogni ricreazione: andrebbe corretto dopo ogni `cluster.sh destroy`,
e dimenticarsene una volta significa configurare qualunque cosa risponda al
vecchio indirizzo.

Si può interrogare anche a mano:

```bash
deploy/ansible/inventory/multipass.py
```

```
[control_plane]
  media-cp   192.168.252.2
[workers]
  media-w1   192.168.252.3
  media-w2   192.168.252.4
```

Una macchina spenta compare con la scritta `spenta` al posto dell'indirizzo, e
i playbook si fermano dicendolo — invece di lasciare scadere una connessione
SSH dopo quindici secondi verso un indirizzo che non c'è.

## I ruoli

| Ruolo | Cosa fa | Traduce |
|---|---|---|
| `machines` | crea le tre macchine con Multipass e ci mette la chiave | `cluster.sh create` |
| `node` | swap, moduli del kernel, containerd, pacchetti Kubernetes | `deploy/cluster/prepare-node.sh` |
| `control_plane` | `kubeadm init`, credenziali, rete fra i pod (Flannel) | `cluster.sh create` |
| `worker` | gettone di aggancio e `kubeadm join` | `cluster.sh create` |
| `addons` | dischi, misuratore, Helm, ingresso, KEDA | `cluster.sh addons`, `ingress`, `keda` |

### I due comandi che non si possono ripetere

`kubeadm init` e `kubeadm join` falliscono su un nodo già configurato, e
forzarli distruggerebbe il cluster. Entrambi i ruoli chiedono prima se serve,
guardando il file che kubeadm scrive quando ha finito:

| Domanda | File |
|---|---|
| il piano di controllo è già nato? | `/etc/kubernetes/admin.conf` |
| l'operaio è già agganciato? | `/etc/kubernetes/kubelet.conf` |

Il gettone di aggancio scade dopo 24 ore, quindi non si conserva: viene chiesto
al piano di controllo al momento, e **solo se almeno un operaio ne ha bisogno**.
La condizione guarda tutti gli operai e non solo il primo — se il primo fosse
già agganciato e il secondo no, chiedere solo per il primo lascerebbe il
secondo senza gettone.

Gli script in `deploy/cluster/` restano nella repo: non sono più la via
principale, ma sono la specifica leggibile di cosa deve succedere, e
`smoke.sh`, `faults.sh` e `burst.sh` sono le verifiche che rendono
dimostrabile tutto il resto.

## Da zero, in due comandi

```bash
cd deploy/ansible         && ansible-playbook site.yml   # ~7 min
deploy/cluster/images.sh load                            # ~25 s
cd deploy/terraform/local && terraform apply             # ~2 min
```

Il passo in mezzo esiste solo perché questo cluster non ha un registro di
immagini: le macchine virtuali non vedono il Docker del Mac. In Fase 7 sparisce.

### Il nome di dominio va aggiornato a mano

Multipass riassegna gli indirizzi a ogni ricreazione, quindi dopo un `destroy`
il nome `media-platform.test` punta quasi sempre alla macchina sbagliata.
Scrivere in `/etc/hosts` chiede i permessi di amministratore e non si può
automatizzare — ma il playbook confronta e, se non coincidono, stampa la riga
esatta da eseguire:

```
ATTENZIONE — media-platform.test non punta a 192.168.252.6.
Riga attuale: «192.168.252.3   media-platform.test».
Serve, con i permessi di amministratore: sudo sed -i '' ...
```

## Tre scelte spiegate

**`gather_facts: false`, poi un task esplicito.** La raccolta dei dati apre la
connessione **prima** di qualunque task: con la raccolta automatica, il
controllo «la macchina è accesa?» non verrebbe mai eseguito e al suo posto si
vedrebbe un errore SSH incomprensibile.

**`inject_facts_as_vars = False`.** I dati raccolti si leggono come
`ansible_facts['distribution']` e non come `ansible_distribution`. È il
comportamento che diventerà l'unico da ansible-core 2.24: adottarlo adesso
costa una parentesi e evita di riscrivere tutto più avanti. Attenzione a non
confonderli con `ansible_host`, che non è un dato raccolto ma una variabile
dell'inventario.

**Gli handler.** Containerd viene riavviato **una volta sola** e solo se la sua
configurazione è cambiata davvero, per quante siano le modifiche che lo
chiedono. Lo script lo riavviava due volte a ogni esecuzione.

## Le versioni

Stanno tutte in `group_vars/all.yml`, e sono le stesse fissate negli script di
`deploy/cluster/`. Finché i due percorsi convivono devono produrre lo stesso
cluster.
