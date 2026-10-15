"""Native Git packet framing for the durable final response gate."""

def packet(payload):
    return f'{len(payload) + 4:04x}'.encode() + payload


def read_exact(stream, size):
    data = bytearray()
    while len(data) < size:
        part = stream.read(size - len(data))
        if not part:
            raise ValueError('truncated native Git response')
        data.extend(part)
    return bytes(data)


def gate(stream, progress, limit=8 * 1024 * 1024):
    """Stream sideband progress, retaining opaque report-status v1/v2 bytes."""
    held = bytearray()
    flushed = False
    while True:
        header = stream.read(4)
        if not header:
            break
        if len(header) < 4:
            header += read_exact(stream, 4 - len(header))
        try:
            size = int(header, 16)
        except ValueError:
            raise ValueError('invalid native Git packet') from None
        if size == 0:
            held.extend(header)
            flushed = True
            # receive-pack ends with a flush. Do not release it early.
            if stream.read(1):
                raise ValueError('output after final native Git flush')
            break
        if size < 4 or size > 65520:
            raise ValueError('invalid native Git packet size')
        payload = read_exact(stream, size - 4)
        if payload[:1] == b'\x02':
            progress(header + payload)
        elif payload[:1] == b'\x03':
            raise ValueError('native Git fatal response')
        else:
            held.extend(header + payload)
        if len(held) > limit:
            raise ValueError('native Git final response exceeds limit')
    if not held or not flushed:
        raise ValueError('native Git has no complete final response')
    return bytes(held)

