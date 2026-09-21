"""Costruzione e accesso all'adapter condiviso dei metadati.

L'api-service questo archivio lo legge soltanto: a scriverlo è il worker.
Quale dei due sia — MongoDB o DynamoDB — lo decide la configurazione.
"""

from flask import current_app


def build_metadata_store(settings):
    return settings.documents.build()


def get_metadata_store():
    return current_app.config["METADATA"]
