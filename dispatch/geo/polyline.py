"""Google polyline с точностью 1e-5: линия маршрута в ~5 раз компактнее списка координат."""


def encode(points):
    out, prev_lat, prev_lon = [], 0, 0
    for lat, lon in points:
        ilat, ilon = round(lat * 1e5), round(lon * 1e5)
        for delta in (ilat - prev_lat, ilon - prev_lon):
            v = ~(delta << 1) if delta < 0 else delta << 1
            while v >= 0x20:
                out.append(chr((0x20 | (v & 0x1f)) + 63))
                v >>= 5
            out.append(chr(v + 63))
        prev_lat, prev_lon = ilat, ilon
    return ''.join(out)


def decode(text):
    points, i, lat, lon = [], 0, 0, 0
    while i < len(text):
        deltas = []
        for _ in range(2):
            shift, value = 0, 0
            while True:
                b = ord(text[i]) - 63
                i += 1
                value |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            deltas.append(~(value >> 1) if value & 1 else value >> 1)
        lat, lon = lat + deltas[0], lon + deltas[1]
        points.append([lat / 1e5, lon / 1e5])
    return points
