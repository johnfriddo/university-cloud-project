#!/usr/bin/env python3
"""Inventario dinamico: chiede a Multipass quali macchine esistono e dove sono.

Un file d'inventario statico conterrebbe tre indirizzi, e Multipass ne
assegna di nuovi ogni volta che una macchina viene ricreata. Scriverli
significa correggere il file dopo ogni `cluster.sh destroy`, e dimenticarsene
una volta significa che Ansible configura qualunque cosa risponda al vecchio indirizzo.

Ansible esegue questo script e legge il JSON che stampa. Gli stessi tre nomi
stanno in `cluster.sh`; qui decidono in quale gruppo finisce ogni macchina,
che è ciò su cui i playbook fanno la propria selezione.

    deploy/ansible/inventory/multipass.py --list    # what Ansible calls
    deploy/ansible/inventory/multipass.py           # lo stesso, leggibile
"""

import json
import shutil
import subprocess
import sys

CONTROL_PLANE = "media-cp"
WORKERS = ("media-w1", "media-w2")

# Le macchine che esistono ma sono spente non hanno indirizzo. Vengono
# segnalate come irraggiungibili invece di essere taciute: «nessun operaio
# trovato» è un messaggio molto più difficile da capire di «media-w1 è spenta».
STOPPED = "spenta"


def machines() -> dict[str, str | None]:
    """Ogni macchina del progetto che Multipass conosce, con il suo indirizzo."""
    if shutil.which("multipass") is None:
        fail("multipass non è installato o non è nel PATH")

    try:
        raw = subprocess.run(
            ["multipass", "list", "--format", "json"],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout
    except subprocess.CalledProcessError as exc:
        fail(f"multipass list è fallito: {exc.stderr.strip()}")
    except subprocess.TimeoutExpired:
        fail("multipass list non ha risposto entro 30 secondi")

    known = {CONTROL_PLANE, *WORKERS}
    found: dict[str, str | None] = {}
    for entry in json.loads(raw)["list"]:
        if entry["name"] in known:
            addresses = entry.get("ipv4") or []
            found[entry["name"]] = addresses[0] if addresses else None
    return found


def inventory() -> dict:
    found = machines()

    hosts: dict[str, dict] = {}
    for name, address in found.items():
        hosts[name] = {"ansible_host": address or STOPPED}

    return {
        "_meta": {"hostvars": hosts},
        # Ogni macchina del cluster: è su queste che gira la preparazione comune.
        "cluster": {"children": ["control_plane", "workers"]},
        "control_plane": {"hosts": [n for n in (CONTROL_PLANE,) if n in found]},
        "workers": {"hosts": [n for n in WORKERS if n in found]},
    }


def fail(message: str) -> None:
    print(f"inventario: {message}", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    data = inventory()
    if "--list" in sys.argv:
        print(json.dumps(data))
        return 0

    # Chiamato a mano: le stesse informazioni, per una persona.
    for group in ("control_plane", "workers"):
        print(f"[{group}]")
        for name in data[group]["hosts"]:
            address = data["_meta"]["hostvars"][name]["ansible_host"]
            print(f"  {name:10} {address}")
    if not data["control_plane"]["hosts"] and not data["workers"]["hosts"]:
        print("nessuna macchina del progetto: lancia 'deploy/cluster/cluster.sh create'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
