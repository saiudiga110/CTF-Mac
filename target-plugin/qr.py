"""Minimal QR encoder (byte mode, ECC M, versions 1-5) for join-URL SVGs."""

# Data codewords, ECC per block, block count, remainder bits.
# Version M: (data_codewords, ecc_per_block, blocks, remainder)
_VERSIONS = {
    1: (16, 10, 1, 0),
    2: (28, 16, 1, 7),
    3: (44, 26, 1, 7),
    4: (64, 18, 2, 7),
    5: (86, 24, 2, 7),
}
_ALIGN = {
    1: [],
    2: [6, 18],
    3: [6, 22],
    4: [6, 26],
    5: [6, 30],
}

_EXP = [0] * 512
_LOG = [0] * 256


def _init_gf():
    x = 1
    for i in range(255):
        _EXP[i] = x
        _LOG[x] = i
        x <<= 1
        if x & 0x100:
            x ^= 0x11D
    for i in range(255, 512):
        _EXP[i] = _EXP[i - 255]


_init_gf()


def _gf_mul(a, b):
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _rs_generator(nsym):
    gen = [1]
    for i in range(nsym):
        nxt = [0] * (len(gen) + 1)
        for j, coef in enumerate(gen):
            nxt[j] ^= coef
            nxt[j + 1] ^= _gf_mul(coef, _EXP[i])
        gen = nxt
    return gen


def _rs_encode(data, nsym):
    gen = _rs_generator(nsym)
    ecc = [0] * nsym
    for byte in data:
        factor = byte ^ ecc[0]
        ecc = ecc[1:] + [0]
        if factor == 0:
            continue
        for j in range(nsym):
            ecc[j] ^= _gf_mul(gen[j + 1], factor)
    return ecc


def _size(version):
    return 21 + 4 * (version - 1)


def _choose_version(payload_bytes):
    # mode(4) + count(8) + data + terminator(4) then padded to codewords
    bits = 4 + 8 + 8 * payload_bytes + 4
    need = (bits + 7) // 8
    for version, (data_cw, _ecc, _blocks, _rem) in _VERSIONS.items():
        if need <= data_cw:
            return version
    raise ValueError("Text is too long for this QR encoder (max ~80 bytes).")


def _bitstream(data, data_cw):
    bits = [0, 1, 0, 0]  # byte mode
    n = len(data)
    bits.extend((n >> (7 - i)) & 1 for i in range(8))
    for byte in data:
        bits.extend((byte >> (7 - i)) & 1 for i in range(8))
    bits.extend([0, 0, 0, 0])
    while len(bits) % 8:
        bits.append(0)
    codewords = []
    for i in range(0, len(bits), 8):
        val = 0
        for b in bits[i:i + 8]:
            val = (val << 1) | b
        codewords.append(val)
    pad = (0xEC, 0x11)
    i = 0
    while len(codewords) < data_cw:
        codewords.append(pad[i % 2])
        i += 1
    return codewords[:data_cw]


def _interleave(codewords, data_cw, ecc_n, blocks):
    block_len = data_cw // blocks
    extra = data_cw % blocks
    data_blocks = []
    ecc_blocks = []
    offset = 0
    for i in range(blocks):
        length = block_len + (1 if i >= blocks - extra else 0)
        # For V4/V5 M, extra is 0 so blocks are equal.
        chunk = codewords[offset:offset + length]
        offset += length
        data_blocks.append(chunk)
        ecc_blocks.append(_rs_encode(chunk, ecc_n))
    out = []
    for i in range(max(len(b) for b in data_blocks)):
        for block in data_blocks:
            if i < len(block):
                out.append(block[i])
    for i in range(ecc_n):
        for block in ecc_blocks:
            out.append(block[i])
    return out


def _in_finder(n, r, c):
    return (
        (r <= 7 and c <= 7)
        or (r <= 7 and c >= n - 8)
        or (r >= n - 8 and c <= 7)
    )


def _alignment_centers(version):
    return _ALIGN[version]


def _in_alignment(version, n, r, c):
    centers = _alignment_centers(version)
    for y in centers:
        for x in centers:
            if _in_finder(n, y, x):
                continue
            if abs(r - y) <= 2 and abs(c - x) <= 2:
                return True
    return False


def _is_function(version, n, r, c):
    if _in_finder(n, r, c):
        return True
    if r == 6 or c == 6:
        return True
    if r == 8 and c == n - 8:
        return True
    if _in_alignment(version, n, r, c):
        return True
    # format info
    if r == 8 and (c <= 8 or c >= n - 8):
        return True
    if c == 8 and (r <= 8 or r >= n - 8):
        return True
    return False


def _place_finders(grid, n):
    pattern = [
        [1, 1, 1, 1, 1, 1, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 1, 1, 1, 0, 1],
        [1, 0, 0, 0, 0, 0, 1],
        [1, 1, 1, 1, 1, 1, 1],
    ]
    origins = ((0, 0), (0, n - 7), (n - 7, 0))
    for orow, ocol in origins:
        for r in range(7):
            for c in range(7):
                grid[orow + r][ocol + c] = pattern[r][c]


def _place_timing(grid, n):
    for i in range(n):
        bit = 1 if i % 2 == 0 else 0
        if grid[6][i] is None:
            grid[6][i] = bit
        if grid[i][6] is None:
            grid[i][6] = bit


def _place_alignment(grid, version, n):
    centers = _alignment_centers(version)
    pat = [
        [1, 1, 1, 1, 1],
        [1, 0, 0, 0, 1],
        [1, 0, 1, 0, 1],
        [1, 0, 0, 0, 1],
        [1, 1, 1, 1, 1],
    ]
    for y in centers:
        for x in centers:
            if _in_finder(n, y, x):
                continue
            for r in range(-2, 3):
                for c in range(-2, 3):
                    grid[y + r][x + c] = pat[r + 2][c + 2]


def _format_bits(mask):
    data = mask  # ECC M = 00, so 5-bit field is just the mask
    rem = data << 10
    gen = 0b10100110111
    for i in range(14, 9, -1):
        if rem & (1 << i):
            rem ^= gen << (i - 10)
    return (data << 10 | (rem & 0x3FF)) ^ 0x5412


def _place_format(grid, n, mask):
    bits = _format_bits(mask)
    coords_a = [
        (8, 0), (8, 1), (8, 2), (8, 3), (8, 4), (8, 5), (8, 7), (8, 8),
        (7, 8), (5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8),
    ]
    coords_b = [
        (n - 1, 8), (n - 2, 8), (n - 3, 8), (n - 4, 8), (n - 5, 8), (n - 6, 8), (n - 7, 8), (n - 8, 8),
        (8, n - 7), (8, n - 6), (8, n - 5), (8, n - 4), (8, n - 3), (8, n - 2), (8, n - 1),
    ]
    for i in range(15):
        bit = (bits >> i) & 1
        r, c = coords_a[i]
        grid[r][c] = bit
        r, c = coords_b[i]
        grid[r][c] = bit
    grid[8][n - 8] = 1


def _mask_bit(mask, r, c):
    if mask == 0:
        return (r + c) % 2 == 0
    if mask == 1:
        return r % 2 == 0
    if mask == 2:
        return c % 3 == 0
    if mask == 3:
        return (r + c) % 3 == 0
    if mask == 4:
        return (r // 2 + c // 3) % 2 == 0
    if mask == 5:
        return (r * c) % 2 + (r * c) % 3 == 0
    if mask == 6:
        return ((r * c) % 2 + (r * c) % 3) % 2 == 0
    return ((r + c) % 2 + (r * c) % 3) % 2 == 0


def _place_data(grid, version, n, bits):
    idx = 0
    total = len(bits)
    upward = True
    col = n - 1
    while col > 0:
        if col == 6:
            col -= 1
        rows = range(n - 1, -1, -1) if upward else range(n)
        for r in rows:
            for c in (col, col - 1):
                if grid[r][c] is not None:
                    continue
                bit = bits[idx] if idx < total else 0
                idx += 1
                grid[r][c] = bit
        upward = not upward
        col -= 2


def _penalty(grid):
    n = len(grid)
    score = 0
    for r in range(n):
        run = 1
        for c in range(1, n):
            if grid[r][c] == grid[r][c - 1]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for c in range(n):
        run = 1
        for r in range(1, n):
            if grid[r][c] == grid[r - 1][c]:
                run += 1
            else:
                if run >= 5:
                    score += 3 + (run - 5)
                run = 1
        if run >= 5:
            score += 3 + (run - 5)
    for r in range(n - 1):
        for c in range(n - 1):
            v = grid[r][c]
            if v == grid[r][c + 1] == grid[r + 1][c] == grid[r + 1][c + 1]:
                score += 3
    dark = sum(cell for row in grid for cell in row)
    percent = (dark * 100) // (n * n)
    score += (abs(percent - 50) // 5) * 10
    return score


def _bits_from_codewords(codewords, remainder):
    bits = []
    for byte in codewords:
        bits.extend((byte >> (7 - i)) & 1 for i in range(8))
    bits.extend([0] * remainder)
    return bits


def encode_matrix(text):
    data = text.encode("utf-8")
    version = _choose_version(len(data))
    data_cw, ecc_n, blocks, remainder = _VERSIONS[version]
    n = _size(version)
    codewords = _bitstream(data, data_cw)
    interleaved = _interleave(codewords, data_cw, ecc_n, blocks)
    data_bits = _bits_from_codewords(interleaved, remainder)

    best = None
    best_score = None
    for mask in range(8):
        grid = [[None] * n for _ in range(n)]
        _place_finders(grid, n)
        _place_alignment(grid, version, n)
        _place_timing(grid, n)
        _place_format(grid, n, mask)
        work = [row[:] for row in grid]
        _place_data(work, version, n, data_bits)
        for r in range(n):
            for c in range(n):
                if work[r][c] is None:
                    work[r][c] = 0
                elif not _is_function(version, n, r, c) and _mask_bit(mask, r, c):
                    work[r][c] ^= 1
        _place_format(work, n, mask)
        score = _penalty(work)
        if best_score is None or score < best_score:
            best_score = score
            best = work
    return best


def matrix_to_svg(matrix, scale=8, border=4):
    n = len(matrix)
    dim = (n + border * 2) * scale
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %s %s" width="%s" height="%s" shape-rendering="crispEdges">'
        % (dim, dim, dim, dim),
        '<rect width="%s" height="%s" fill="#fff"/>' % (dim, dim),
    ]
    for y, row in enumerate(matrix):
        for x, dark in enumerate(row):
            if not dark:
                continue
            parts.append(
                '<rect x="%s" y="%s" width="%s" height="%s" fill="#000"/>'
                % ((x + border) * scale, (y + border) * scale, scale, scale)
            )
    parts.append("</svg>")
    return "".join(parts)


def svg_for_text(text, scale=8, border=4):
    if not text:
        return ""
    return matrix_to_svg(encode_matrix(text), scale=scale, border=border)
