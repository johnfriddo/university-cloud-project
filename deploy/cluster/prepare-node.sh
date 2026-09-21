#!/usr/bin/env bash
#
# Prepara una macchina Ubuntu a diventare un nodo Kubernetes.
#
# Gira dentro ogni macchina virtuale, come root, prima di kubeadm. Ogni passo
# qui sotto è qualcosa che kubeadm controlla e senza cui si rifiuta di
# procedere. In Fase 6 questi stessi passi diventano un ruolo Ansible: questo script ne è la specifica.
#
# Usage: prepare-node.sh v1.36
#
set -euo pipefail

K8S_MINOR="${1:?serve la versione di Kubernetes, per esempio v1.36}"
export DEBIAN_FRONTEND=noninteractive

echo ">>> $(hostname): preparazione del nodo (Kubernetes ${K8S_MINOR})"

# --- 1. no swap ----------------------------------------------------------------
# Il kubelet si rifiuta di partire con lo swap acceso: la sua contabilità della
# memoria dà per scontato che ciò che un container usa sia davvero in RAM. Le
# immagini di Multipass arrivano senza swap, ma un nodo non deve dipendere da questo.
swapoff -a
sed -i '/\sswap\s/ s/^/#/' /etc/fstab

# --- 2. kernel modules and forwarding --------------------------------------------
# overlay: il filesystem con cui sono costruiti i container.
# br_netfilter e i sysctl: il traffico che attraversa i ponti virtuali fra i pod
# deve passare da iptables, altrimenti i Service non funzionerebbero fra nodi.
cat > /etc/modules-load.d/kubernetes.conf <<'EOF'
overlay
br_netfilter
EOF
modprobe overlay
modprobe br_netfilter

cat > /etc/sysctl.d/kubernetes.conf <<'EOF'
net.bridge.bridge-nf-call-iptables  = 1
net.bridge.bridge-nf-call-ip6tables = 1
net.ipv4.ip_forward                 = 1
EOF
sysctl --system > /dev/null

# --- 3. container runtime: containerd -------------------------------------------
apt-get update -q
apt-get install -y -q containerd apt-transport-https ca-certificates curl gpg > /dev/null

mkdir -p /etc/containerd
containerd config default > /etc/containerd/config.toml
# Il kubelet e containerd devono essere d'accordo su chi governa i cgroup (il
# che limita memoria e processore per container). Ubuntu usa systemd per quello,
# e due gestori affiancati rendono il nodo instabile sotto pressione — che è
# esattamente la situazione in cui 8 GB ci mettono.
sed -i 's/SystemdCgroup = false/SystemdCgroup = true/' /etc/containerd/config.toml
systemctl restart containerd
systemctl enable containerd > /dev/null

# --- 4. kubelet, kubeadm, kubectl -----------------------------------------------
# Dal repository ufficiale di Kubernetes, fissato a una versione minore: i
# pacchetti di Ubuntu restano indietro, e un cluster i cui nodi girano versioni diverse è un
# cluster che cerca guai.
mkdir -p /etc/apt/keyrings
curl -fsSL "https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/Release.key" \
  | gpg --dearmor --yes -o /etc/apt/keyrings/kubernetes.gpg
echo "deb [signed-by=/etc/apt/keyrings/kubernetes.gpg] https://pkgs.k8s.io/core:/stable:/${K8S_MINOR}/deb/ /" \
  > /etc/apt/sources.list.d/kubernetes.list

apt-get update -q
apt-get install -y -q kubelet kubeadm kubectl > /dev/null
# Bloccati: un aggiornamento automatico non deve mai spostare un nodo su una versione diversa.
apt-mark hold kubelet kubeadm kubectl > /dev/null

# L'immagine «pause» è il container vuoto attorno a cui è costruito ogni pod.
# kubeadm si aspetta una versione precisa; il predefinito di containerd è più
# vecchio, e la differenza è un avviso a ogni avvio. Si allineano.
#
# La chiave ha cambiato nome fra containerd 1.x («sandbox_image») e 2.x
# («sandbox»). Sono gestite entrambe: un sed che non trova niente non fa
# niente, e non lo dice, quindi il risultato si controlla subito dopo.
pause_image="$(kubeadm config images list 2>/dev/null | grep pause)"
sed -i -E \
  -e "s#^(\s*)sandbox_image = .*#\1sandbox_image = \"${pause_image}\"#" \
  -e "s#^(\s*)sandbox = .*#\1sandbox = '${pause_image}'#" \
  /etc/containerd/config.toml
grep -q "${pause_image}" /etc/containerd/config.toml \
  || { echo "!!! immagine pause non allineata: formato di configurazione inatteso" >&2; exit 1; }
grep -q "SystemdCgroup = true" /etc/containerd/config.toml \
  || { echo "!!! gestore dei cgroup non impostato: formato di configurazione inatteso" >&2; exit 1; }
systemctl restart containerd

systemctl enable --now kubelet > /dev/null

echo ">>> $(hostname): pronto — containerd $(containerd --version | awk '{print $3}'), kubeadm $(kubeadm version -o short)"
