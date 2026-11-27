# Azure Blob Storage manual verification

This checklist verifies the Azure Blob Storage filesystem against a real storage account, because the Azurite emulator does not exercise real sign-in.
The steps are run by hand and each result is recorded in the result block at the end.

## Purpose

The Azure filesystem connects, lists, copies, edits, moves and erases blobs through `azure.storage.blob`.
Automated tests cover these functions with an emulator and with mocks.
This checklist covers what they cannot: `DefaultAzureCredential` sign-in with `az login`, real account behaviour and real network conditions.

## Prerequisites

- A storage account and a disposable container that holds no data you need.
- The role "Storage Blob Data Contributor" on that container for the signed-in user.
- Azure CLI installed and `az login` run with that user.
- The application installed with the `azure` extra, for example `uv tool install 'nova-navigator[azure]'`.
- The version and commit under test, recorded in the result block.

## Steps

Each step lists the action, the expected result and a result field.
Mark a step `pass` or `fail`, and write a short note for any failure.

1. **Connect through saved settings.**
   Action: add the account URL, the container and the sign-in option in the remote settings, then connect.
   Expected: the container root opens without an error.
   Result: ____
2. **Connect through an `azure://` URI.**
   Action: open `azure://<account>.blob.core.windows.net/<container>/`.
   Expected: the container root opens.
   Result: ____
3. **List the root and a nested directory.**
   Action: upload names with spaces and special characters, such as `sp ace/ü & 'q'.txt`, then list the root and the nested directory.
   Expected: every name appears exactly once with the right size.
   Result: ____
4. **Copy a file and a tree in both directions.**
   Action: copy a local file and a local directory tree to the container, then copy them back to a new local directory.
   Expected: the content of each file matches the original (for example with `cmp`).
   Result: ____
5. **Edit, save and verify.**
   Action: open a blob in the editor, change it, save, then run `az storage blob download` for the same blob.
   Expected: the downloaded content equals the saved content.
   Result: ____
6. **Conflict on a changed blob.**
   Action: open a blob, change it elsewhere (with `az storage blob upload --overwrite` or the portal), then save the local edit.
   Expected: the application reports a conflict and does not overwrite the blob silently.
   Result: ____
7. **Create a directory.**
   Action: create a new directory in the container.
   Expected: the directory is listed.
   Result: ____
8. **Rename and move a file and a directory.**
   Action: rename a file, move it into another directory, then rename and move a directory with content.
   Expected: the new names hold the content and the old names are gone.
   Result: ____
9. **Delete a file and a non-empty directory.**
   Action: delete one file and one directory that contains files.
   Expected: both disappear from the listing and from `az storage blob list`.
   Result: ____

## Observations

Record these values.
They do not pass or fail on their own.

- Time from connect to the first listing, once with the managed-identity option off and once with it on.
  The default credential chain can wait for the managed-identity endpoint before it tries the Azure CLI, which makes the first access slow.
  Observed on the development host with `HTTPS_PROXY` set in the environment: about 90 s for the first access.
- Whether the `.keep` marker of a directory is listed as a hidden file, and whether that is acceptable.
- Behaviour after `az logout`: the expected result is an authentication error that names the sign-in step.
- Behaviour with a wrong container name: the expected result is a "No such file or directory" style error, not an empty listing.

## Result block

```text
Date:
Application version and commit:
Azure CLI version:
Account type (host-style URL, region):
Steps 1-9 outcome:
Observations:
Decision on failed steps (fix, or present Azure as experimental):
```
