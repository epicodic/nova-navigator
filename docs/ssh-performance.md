# SSH File Transfer Performance

## Why transfers are slow by default

The file copy loop in `filemanager/tasks.py` reads a 64 KB chunk from the source and writes it to the destination, then repeats.
When the destination is SSH, `SSHFilesystem.write()` returns a `paramiko.SFTPFile` opened in `"wb"` mode.
By default, paramiko SFTP uses **stop-and-wait**: each `write()` call sends one `SSH_FXP_WRITE` packet, then blocks waiting for the server's ACK before sending the next packet.
On any connection with non-trivial latency every 64 KB chunk burns a full round-trip time (RTT).
Throughput is bounded by `chunk_size / RTT` rather than the available bandwidth.

Midnight Commander uses `libssh2` with a sliding-window / pipelined write mode.
It sends many outstanding write requests without waiting for individual ACKs, keeping the TCP pipe full.

---

## Fix 1 — Enable paramiko pipelining (recommended)

`SFTPFile.set_pipelined(True)` tells paramiko to fire `SSH_FXP_WRITE` packets without waiting for per-packet ACKs.
Data still goes onto the wire on every `writer.write()` call; TCP back-pressure throttles the sender if the remote receive buffer fills.
The progress bar in `copy_file` continues to advance proportionally to actual data in-flight.
At `close()` paramiko drains the final ACKs, which may cause a brief pause at 100% — acceptable.

```python
# SSHFilesystem.write() in vfs/filesystems/ssh.py
def write(self, path: VPath) -> StreamWriterLike:
    f = self._sftp_client.open(path.path.as_posix(), "wb")
    f.set_pipelined(True)
    return cast("StreamWriterLike", f)
```

---

## Fix 2 — Increase the SSH transport window size (complementary)

The underlying SSH channel has a flow-control window (default 2 MB in paramiko).
For large files over fast links the window fills and stalls the sender even when pipelining is on.
Setting a larger window at connect time removes this second bottleneck.

```python
# SSHFilesystem.__init__() after self._ssh_client.connect(...)
transport = self._ssh_client.get_transport()
if transport is not None:
    transport.window_size = 64 * 1024 * 1024  # 64 MB
```

Both fixes are orthogonal and should be applied together.

---

## Alternative — `SFTPClient.putfo()` with callback

Paramiko's higher-level `putfo(fl, remote_path, file_size, callback)` method uses pipelining internally and exposes an explicit progress callback:

```python
def callback(sent: int, total: int) -> None:
    ctx.status.set_step_progress(sent, total)

sftp.putfo(src_stream, remote_path, file_size=src_stat.size, callback=callback)
```

This is more invasive: it requires the full source stream upfront and bypasses the `StreamWriterLike` abstraction in `copy_file`, so it would need a separate fast-path for local→SSH transfers.
Not recommended unless Fix 1 + Fix 2 prove insufficient.

---

## Not recommended — asyncssh

`asyncssh` is a fully async SSH/SFTP library with proper concurrent request handling and native async progress callbacks.
Maximum throughput, but it is a significant dependency change and would require rewriting `SSHFilesystem` entirely.
