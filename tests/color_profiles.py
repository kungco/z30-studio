"""Original synthetic matrix/TRC ICC fixture, generated without binary assets."""
import struct
import numpy as np
# Independent W3C CSS Color 4 reference matrices, not the production xy solver.
# https://www.w3.org/TR/css-color-4/#color-conversion-code
A98_XYZ = np.array([[573536 / 994567, 263643 / 1420810, 187206 / 994567],
                    [591459 / 1989134, 6239551 / 9945670, 374412 / 4972835],
                    [53769 / 1989134, 351524 / 4972835, 4929758 / 4972835]])
SRGB_XYZ = np.array([[.4123907992659595, .35758433938387796, .1804807884018343],
                     [.21263900587151036, .7151686787677559, .07219231536073371],
                     [.01933081871559185, .11919477979462599, .9505321522496607]])
A98_TO_SRGB = np.linalg.solve(SRGB_XYZ, A98_XYZ)


def wide_rgb_profile():
    # ICC v2 D50 PCS; Bradford-adapt the synthetic D65 RGB matrix to D50.
    matrix = A98_XYZ
    bradford = np.array([[.8951, .2664, -.1614], [-.7502, 1.7135, .0367], [.0389, -.0685, 1.0296]])
    d65, d50 = np.array([.9504559, 1, 1.0890578]), np.array([.9642, 1, .8249])
    matrix = np.linalg.solve(bradford, np.diag((bradford @ d50) / (bradford @ d65)) @ bradford) @ matrix
    def xyz(v):
        return b'XYZ ' + bytes(4) + struct.pack('>3i', *(round(x * 65536) for x in v))
    desc = b'Z30 synthetic wide RGB\0'
    tags = {b'wtpt': xyz(d50), b'desc': b'desc' + bytes(4) + struct.pack('>I', len(desc)) + desc + bytes(78),
            b'cprt': b'text' + bytes(4) + b'Original generated test fixture\0'}
    for i, c in enumerate('rgb'):
        tags[(c + 'XYZ').encode()] = xyz(matrix[:, i])
        tags[(c + 'TRC').encode()] = b'curv' + bytes(4) + struct.pack('>IH', 1, round(2.2 * 256))
    header = bytearray(128)
    header[8:12] = bytes([2, 0x10, 0, 0]); header[12:24] = b'mntrRGB XYZ '
    header[24:36] = struct.pack('>6H', 2026, 1, 1, 0, 0, 0)
    header[36:40] = b'acsp'; header[68:80] = struct.pack('>3i', *(round(x * 65536) for x in d50))
    offset = 128 + 4 + len(tags) * 12
    table, payload = bytearray(struct.pack('>I', len(tags))), bytearray()
    for name, data in tags.items():
        table += name + struct.pack('>II', offset + len(payload), len(data))
        payload += data + bytes((-len(data)) % 4)
    profile = header + table + payload
    struct.pack_into('>I', profile, 0, len(profile))
    return bytes(profile)
