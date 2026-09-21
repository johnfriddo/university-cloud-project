{{/*
Etichette comuni a ogni oggetto del chart. Servono a rispondere a domande
banali ma frequenti: "cos'è questo pod", "chi ce l'ha messo", "di quale
versione". Sono i nomi standard di Kubernetes, non inventati qui.
*/}}
{{- define "media-platform.labels" -}}
app.kubernetes.io/name: media-platform
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version }}
{{- end -}}

{{/*
Le etichette con cui un Deployment riconosce i propri pod. Sono immutabili
dopo la creazione, quindi contengono il minimo indispensabile: aggiungerci la
versione costringerebbe a cancellare e ricreare a ogni aggiornamento.

Si usa come: {{ include "media-platform.selectorLabels" (dict "ctx" . "component" "api-service") }}
*/}}
{{- define "media-platform.selectorLabels" -}}
app.kubernetes.io/name: media-platform
app.kubernetes.io/instance: {{ .ctx.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{/*
L'immagine di uno dei quattro servizi costruiti da noi.
*/}}
{{- define "media-platform.image" -}}
{{- printf "%s/%s:%s" .ctx.Values.image.repository .service .ctx.Values.image.tag -}}
{{- end -}}

{{/*
La configurazione che tutti i contenitori Python ricevono: i valori in chiaro
dalla ConfigMap, le credenziali e gli indirizzi che le contengono dal Secret.

Un solo blocco per tutti e quattro perché la configurazione è quasi tutta
comune, esattamente come in docker compose (il blocco &app-env).
*/}}
{{- define "media-platform.envFrom" -}}
- configMapRef:
    name: app-config
- secretRef:
    name: app-secrets
{{- end -}}

{{/*
Su quali nodi può girare un pod. Vuoto in locale; su AWS solo nodi ARM.
Si usa dentro `spec:` del pod: {{ include "media-platform.nodeSelector" . | nindent 6 }}
*/}}
{{- define "media-platform.nodeSelector" -}}
{{- with .Values.nodeSelector }}
nodeSelector:
  {{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{/*
Le impronte di ConfigMap e Secret, messe fra le annotazioni dei pod.

Senza, cambiare una password e reinstallare non riavvierebbe niente: i pod
continuerebbero con i vecchi valori, perché il loro Deployment non è cambiato.
Con l'impronta, ogni modifica alla configurazione cambia il Deployment e
provoca un aggiornamento graduale.
*/}}
{{- define "media-platform.configChecksums" -}}
checksum/config: {{ include (print .Template.BasePath "/configmap.yaml") . | sha256sum }}
checksum/secret: {{ include (print .Template.BasePath "/secret.yaml") . | sha256sum }}
{{- end -}}
