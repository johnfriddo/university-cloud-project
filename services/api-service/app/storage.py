"""Costruzione e accesso all'adapter condiviso dell'archivio."""

from flask import current_app

from media_common import ObjectStorage


def build_storage(settings) -> ObjectStorage:
    return ObjectStorage(
        endpoint_internal=settings.s3_endpoint_internal,
        endpoint_public=settings.s3_endpoint_public,
        access_key=settings.s3_access_key,
        secret_key=settings.s3_secret_key,
        region=settings.s3_region,
        bucket_originals=settings.s3_bucket_originals,
        bucket_derived=settings.s3_bucket_derived,
        addressing_style=settings.s3_addressing_style,
    )


def get_storage() -> ObjectStorage:
    return current_app.config["STORAGE"]
