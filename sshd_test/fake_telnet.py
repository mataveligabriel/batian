"""Tiny fake telnet device for lab tests: IAC negotiation, login prompt, echoes commands. Listens on 127.0.0.1:2323."""
import asyncio

IAC, DO, WILL, SB, SE = 255, 253, 251, 250, 240


async def handle(reader, writer):
    writer.write(bytes([IAC, WILL, 1, IAC, WILL, 3, IAC, DO, 24, IAC, DO, 31]))
    await writer.drain()
    buf = b""

    async def readline():
        nonlocal buf
        while b"\n" not in buf:
            data = await reader.read(1024)
            if not data:
                return None
            # strip IAC negotiation
            out = bytearray(); i = 0
            while i < len(data):
                if data[i] == IAC and i + 1 < len(data):
                    if data[i + 1] == SB:
                        end = data.find(bytes([IAC, SE]), i); i = (end + 2) if end != -1 else len(data)
                    else:
                        i += 3
                else:
                    out.append(data[i]); i += 1
            buf += bytes(out)
        line, _, buf = buf.partition(b"\n")
        return line.strip(b"\r")

    writer.write(b"\r\nFakeOS Telnet Lab\r\nUsername: "); await writer.drain()
    user = await readline()
    writer.write(b"Password: "); await writer.drain()
    pw = await readline()
    if user != b"labuser" or pw != b"labpass":
        writer.write(b"\r\nLogin incorrect\r\n"); await writer.drain(); writer.close(); return
    writer.write(b"\r\nWelcome labuser\r\nfake-router# "); await writer.drain()
    while True:
        line = await readline()
        if line is None:
            break
        if line == b"show version":
            writer.write(b"\r\nFakeOS 1.0 uptime 3 days\r\n")
        elif line == b"terminal length 0":
            pass
        elif line:
            writer.write(b"\r\nECHO:" + line + b"\r\n")
        writer.write(b"fake-router# ")
        await writer.drain()
    writer.close()


async def main():
    server = await asyncio.start_server(handle, "127.0.0.1", 2323)
    async with server:
        await server.serve_forever()

asyncio.run(main())
