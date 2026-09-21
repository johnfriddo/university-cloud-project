"""Le risposte d'errore condivise da ogni endpoint.

L'API risponde sempre in JSON, errori compresi: il frontend deve poter mostrare
il motivo di un guasto senza analizzare una pagina HTML. `code` serve al client
per decidere cosa fare, `message` è quello che legge l'utente.
"""

from flask import jsonify

# Quello che legge l'utente per i guasti che Flask solleva da sé.
HTTP_MESSAGES = {
    400: "Richiesta non valida",
    401: "Autenticazione richiesta",
    403: "Accesso non consentito",
    404: "Risorsa non trovata",
    405: "Metodo non consentito",
    413: "File troppo grande",
    415: "Formato non supportato",
    429: "Troppe richieste, riprova tra poco",
}


def error_response(status: int, code: str, message: str):
    return jsonify(error={"code": code, "message": message}), status
