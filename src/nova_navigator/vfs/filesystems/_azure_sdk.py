"""The only module that imports the Azure SDK; load it through `azure._sdk()`."""

from azure.core.exceptions import HttpResponseError, ResourceExistsError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobPrefix, BlobProperties, ContainerClient, StorageStreamDownloader

__all__ = [
    "BlobPrefix",
    "BlobProperties",
    "ContainerClient",
    "DefaultAzureCredential",
    "HttpResponseError",
    "ResourceExistsError",
    "ResourceNotFoundError",
    "StorageStreamDownloader",
]
