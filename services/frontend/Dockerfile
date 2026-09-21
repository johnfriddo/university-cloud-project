# Due stadi: Node compila, Nginx serve. Nell'immagine finale non restano né
# Node né le dipendenze di build, solo i file statici prodotti.
FROM node:24-alpine AS build

WORKDIR /build

# Prima il manifesto delle dipendenze: cambia di rado, e il livello con
# node_modules resta in cache mentre il codice cambia in continuazione.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .
RUN npm run build

FROM nginx:1.27-alpine

# Non una configurazione finita ma un modello: l'immagine nginx sostituisce i
# segnaposto ${...} all'avvio con le variabili d'ambiente, e scrive il
# risultato in conf.d/. Serve perché l'indirizzo del DNS interno cambia fra
# Docker e Kubernetes, e un'immagine che nomina quello di Docker non è
# un'immagine che si può installare sul cluster.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /build/dist/frontend/browser /usr/share/nginx/html

# L'indirizzo del DNS non lo dichiariamo più: questa variabile accende uno
# script che l'immagine nginx porta già con sé, il quale legge i nameserver da
# /etc/resolv.conf e li mette in ${NGINX_LOCAL_RESOLVERS}, che il modello usa.
#
# Serviva perché quell'indirizzo cambia in ogni ambiente — 127.0.0.11 in
# Docker, il servizio kube-dns sul cluster locale — e su EKS Auto Mode non
# esiste proprio un servizio da nominare. Il container quell'indirizzo lo sa
# già: non è una decisione da configurare, è un fatto da leggere.
ENV NGINX_ENTRYPOINT_LOCAL_RESOLVERS=1 \
    API_UPSTREAM=http://api-service:8000

EXPOSE 8080
