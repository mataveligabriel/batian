"""Gerador de QR code (modo byte, correção M, versões 1–10) em Python puro, sem dependências.

Usado para o QR do 2FA (otpauth://…). Saída em SVG.
"""
from typing import List, Optional

# versão -> (codewords de EC por bloco, [(qtd de blocos, codewords de dados por bloco), ...])  — nível M
_EC_M = {
    1: (10, [(1, 16)]), 2: (16, [(1, 28)]), 3: (26, [(1, 44)]), 4: (18, [(2, 32)]), 5: (24, [(2, 43)]),
    6: (16, [(4, 27)]), 7: (18, [(4, 31)]), 8: (22, [(2, 38), (2, 39)]), 9: (22, [(3, 36), (2, 37)]),
    10: (26, [(4, 43), (1, 44)]),
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}

# GF(256), polinômio 0x11d
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _gmul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(n: int) -> List[int]:
    g = [1]
    for i in range(n):
        ng = [0] * (len(g) + 1)
        for j, c in enumerate(g):
            ng[j] ^= c
            ng[j + 1] ^= _gmul(c, _EXP[i])
        g = ng
    return g


def _rs_ec(data: List[int], n: int) -> List[int]:
    gen = _rs_generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _gmul(gen[j], coef)
    return rem[len(data):]


def _data_capacity(v: int) -> int:
    return sum(c * d for c, d in _EC_M[v][1])


def _encode_bits(data: bytes, v: int) -> List[int]:
    bits: List[int] = []

    def put(val: int, n: int):
        bits.extend((val >> (n - 1 - i)) & 1 for i in range(n))
    put(0b0100, 4)
    put(len(data), 8 if v <= 9 else 16)
    for b in data:
        put(b, 8)
    cap = _data_capacity(v) * 8
    put(0, min(4, cap - len(bits)))
    while len(bits) % 8:
        bits.append(0)
    pads, k = (0xEC, 0x11), 0
    while len(bits) < cap:
        put(pads[k % 2], 8)
        k += 1
    return bits


def _codewords(data: bytes, v: int) -> List[int]:
    bits = _encode_bits(data, v)
    words = [int("".join(map(str, bits[i:i + 8])), 2) for i in range(0, len(bits), 8)]
    ecn, groups = _EC_M[v]
    blocks, pos = [], 0
    for cnt, size in groups:
        for _ in range(cnt):
            blocks.append(words[pos:pos + size])
            pos += size
    ecs = [_rs_ec(b, ecn) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ecn):
        out += [e[i] for e in ecs]
    return out


def _bch(value: int, poly: int, bits: int) -> int:
    msb = poly.bit_length() - 1
    v = value << msb
    while v.bit_length() > msb:
        v ^= poly << (v.bit_length() - poly.bit_length())
    return (value << msb) | v


_MASKS = [
    lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0, lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0, lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0, lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _matrix(data: bytes, v: int, mask: int):
    n = 17 + 4 * v
    m: List[List[Optional[int]]] = [[None] * n for _ in range(n)]
    reserved = [[False] * n for _ in range(n)]

    def setf(r, c, val):
        m[r][c] = val
        reserved[r][c] = True

    def finder(r0, c0):
        for r in range(-1, 8):
            for c in range(-1, 8):
                rr, cc = r0 + r, c0 + c
                if 0 <= rr < n and 0 <= cc < n:
                    on = (0 <= r <= 6 and c in (0, 6)) or (0 <= c <= 6 and r in (0, 6)) or (2 <= r <= 4 and 2 <= c <= 4)
                    setf(rr, cc, 1 if on else 0)
    finder(0, 0)
    finder(0, n - 7)
    finder(n - 7, 0)
    for i in range(8, n - 8):
        setf(6, i, 1 if i % 2 == 0 else 0)
        setf(i, 6, 1 if i % 2 == 0 else 0)
    pos = _ALIGN[v]
    last = len(pos) - 1
    for i, r in enumerate(pos):
        for j, c in enumerate(pos):
            if (i == 0 and j == 0) or (i == 0 and j == last) or (i == last and j == 0):
                continue                                   # cantos com os padrões de localização
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    setf(r + dr, c + dc, 1 if max(abs(dr), abs(dc)) != 1 else 0)
    setf(n - 8, 8, 1)                                   # módulo escuro fixo
    # áreas de formato/versão (reservadas; preenchidas depois)
    for i in range(9):
        if not reserved[8][i]:
            reserved[8][i] = True
        if not reserved[i][8]:
            reserved[i][8] = True
    for i in range(8):
        reserved[8][n - 1 - i] = True
        reserved[n - 1 - i][8] = True
    if v >= 7:
        for i in range(6):
            for j in range(3):
                reserved[i][n - 11 + j] = True
                reserved[n - 11 + j][i] = True
    # dados em zigue-zague
    bits = []
    for w in _codewords(data, v):
        bits.extend((w >> (7 - i)) & 1 for i in range(8))
    k, up, c = 0, True, n - 1
    while c > 0:
        if c == 6:
            c -= 1
        for i in range(n):
            r = n - 1 - i if up else i
            for cc in (c, c - 1):
                if not reserved[r][cc]:
                    b = bits[k] if k < len(bits) else 0
                    k += 1
                    if _MASKS[mask](r, cc):
                        b ^= 1
                    m[r][cc] = b
        up = not up
        c -= 2
    # formato: nível M = 00
    fmt = _bch((0b00 << 3) | mask, 0x537, 15) ^ 0x5412
    fb = [(fmt >> (14 - i)) & 1 for i in range(15)]
    coords1 = [(8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8), (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)]
    coords2 = [(n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8), (n - 6, 8), (n - 7, 8),
               (8, n - 8), (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4), (8, n - 3), (8, n - 2), (8, n - 1)]
    for i in range(15):
        r, cc = coords1[i]
        m[r][cc] = fb[i]
        r, cc = coords2[i]
        m[r][cc] = fb[i]
    if v >= 7:
        vi = _bch(v, 0x1F25, 18)
        for i in range(18):
            bit = (vi >> i) & 1
            a, b = i // 3, i % 3
            m[a][n - 11 + b] = bit
            m[n - 11 + b][a] = bit
    return [[x or 0 for x in row] for row in m]


def _penalty(m) -> int:
    n, p = len(m), 0
    for grid in (m, [list(x) for x in zip(*m)]):
        for row in grid:
            run, prev = 0, None
            for x in row:
                if x == prev:
                    run += 1
                else:
                    if run >= 5:
                        p += run - 2
                    run, prev = 1, x
            if run >= 5:
                p += run - 2
            s = "".join(map(str, row))
            p += 40 * (s.count("10111010000") + s.count("00001011101"))
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                p += 3
    dark = sum(map(sum, m)) * 100 // (n * n)
    p += abs(dark - 50) // 5 * 10
    return p


def make(text: str) -> List[List[int]]:
    data = text.encode("utf-8")
    for v in range(1, 11):
        if len(data) + (2 if v <= 9 else 3) <= _data_capacity(v):
            break
    else:
        raise ValueError("Texto longo demais para o QR (máx. ~210 bytes)")
    best = min((_matrix(data, v, k) for k in range(8)), key=_penalty)
    return best


def svg(text: str, scale: int = 5, border: int = 4, dark: str = "#0f172a", light: str = "#ffffff") -> str:
    m = make(text)
    n = len(m)
    size = (n + 2 * border) * scale
    path = "".join(f"M{(c + border) * scale},{(r + border) * scale}h{scale}v{scale}h-{scale}z"
                   for r in range(n) for c in range(n) if m[r][c])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="{light}"/>'
            f'<path d="{path}" fill="{dark}"/></svg>')
