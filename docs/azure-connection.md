# Azure Blob Storage Connection

Notes on how the Azure Blob Storage connection is established, what credentials are used, and known performance pitfalls.

---

## Connection flow

When the user selects an Azure remote from the *Connect to…* dialog, `_action_connect_to` in `nova_navigator.py` dispatches to `connect_azure` in `remotes/azure.py`.

`connect_azure` reads `conn.azure` (account URL and container name) and `conn.proxy` from the saved `RemoteConnection`, then calls `AzureFilesystem(account_url, container, proxy_url=...)` in a thread via `asyncio.to_thread`.

`AzureFilesystem.__init__` constructs a `DefaultAzureCredential` and a `ContainerClient`.
The client is not connected at construction time; the first actual network call happens when the directory listing is requested.

---

## Authentication

`DefaultAzureCredential` is used.
It walks a chain of credential providers in order until one succeeds:

1. `EnvironmentCredential` — reads `AZURE_CLIENT_ID`, `AZURE_TENANT_ID`, `AZURE_CLIENT_SECRET` (or cert) from the environment.
2. `WorkloadIdentityCredential` — for Kubernetes workload identity.
3. `ManagedIdentityCredential` — probes the IMDS endpoint `http://169.254.169.254/metadata/...`.
   Only available when running inside an Azure VM, ACI, or similar managed environment.
4. `SharedTokenCacheCredential` — reads from the OS credential store.
5. `VisualStudioCodeCredential` — reads the VS Code Azure account token.
6. `AzureCliCredential` — shells out to `az account get-access-token`.
7. `AzurePowerShellCredential` — shells out to `Get-AzAccessToken`.

On a developer workstation the chain typically falls through to `AzureCliCredential`.
Run `az login` once to authenticate; the token is cached and reused.

---

## Proxy handling

A proxy URL (`http://host:port`) can be configured per connection in *Edit Remote Connections*.
It is stored in `ProxySettings` (`host`, `port`) on `RemoteConnection`.

`connect_azure` builds the proxy URL string and passes it as `proxy_url` to `AzureFilesystem`.
`AzureFilesystem.__init__` passes `proxies={"http": proxy_url, "https": proxy_url}` to both `DefaultAzureCredential` and `ContainerClient` so all SDK traffic (auth and storage) is routed through the proxy.

---

## Known pitfall: IMDS timeout when a proxy is configured

`169.254.169.254` (the Azure IMDS / managed identity endpoint) is a link-local address.
It cannot be routed through any proxy.
When a proxy is configured, `ManagedIdentityCredential` will still attempt to reach IMDS — via the proxy — and receive a `502 Network is unreachable` from the proxy.
The SDK retries with exponential backoff (1 s → 2 s → 4 s → 8 s → …) before giving up, adding ~20–25 seconds of dead time before the chain reaches `AzureCliCredential`.

**Default behaviour:** when `proxy_url` is set and `use_managed_identity` is `False` (the default), `DefaultAzureCredential` is constructed with `exclude_managed_identity_credential=True`.
IMDS is skipped entirely; the chain falls through to the next working credential immediately.

---

## Using Managed Identity

If you are running Nova Navigator inside an Azure-managed environment (VM, ACI, AKS pod, App Service, etc.) you can authenticate using the environment's assigned managed identity.

Enable the **Use Managed Identity** checkbox in *Edit Remote Connections* for the Azure entry.
This sets `use_managed_identity = true` in `remotes.toml` and causes `AzureFilesystem` to include `ManagedIdentityCredential` in the credential chain regardless of proxy settings.

### Managed identity + proxy

If a proxy is also configured and you enable managed identity, the IMDS endpoint (`169.254.169.254`) must bypass the proxy at the OS / network level.
The standard way to do this is the `NO_PROXY` environment variable:

```sh
export NO_PROXY="169.254.169.254"
```

Add this to your shell profile or the systemd unit that launches Nova Navigator.
Nova Navigator does not set `NO_PROXY` automatically — the SDK would need it set before the process starts.

---

## Directory listing performance

`AzureFilesystem.iterdir()` calls `ContainerClient.walk_blobs(delimiter="/")` which returns a flat paginated response containing `BlobProperties` (files) and `BlobPrefix` (virtual directories) for the requested prefix.
This is a single paginated API call regardless of the number of items.

The `BlobProperties` objects already carry `size` and `last_modified`.
`iterdir` pre-populates `vpath._stat` on every returned `VPath` from the data already in the listing response.
This means the directory browser can render all columns (name, size, mtime) without making any further API calls per item.

Without this pre-population, each item's `.stat` access would trigger a separate `get_blob_properties()` round-trip — one HTTP call per file — making large directories unusably slow.
