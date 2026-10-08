"""Unity <-> TwinScene (Godot) coordinates.

Measured with tests/assets/axis_marker.obj (points at +X=1, +Y=2, +Z=3) imported into both editors:
    Godot 4.7 keeps (1, 0, 0), (0, 2, 0), (0, 0, 3)
    Unity 6.6 gives (-1, 0, 0), (0, 2, 0), (0, 0, 3)        -> Unity's importers mirror X.
So a mirror in X maps Unity space to Godot space and shared model files line up with no correction:
    position (x, y, z)    -> (-x, y, z)
    rotation (x, y, z, w) -> (x, -y, -z, w)
    scale                 unchanged

Things that point somewhere differ by a half turn: a Unity camera or light looks down its local +Z, a Godot one down
-Z. Their nodes get a 180 degree turn about Y ("correction"), and the children of a corrected node get the inverse
so they stay where they were.
"""

from __future__ import annotations

import math

Vec3 = tuple[float, float, float]
Quat = tuple[float, float, float, float]

IDENTITY: Quat = (0.0, 0.0, 0.0, 1.0)
HALF_TURN_Y: Quat = (0.0, 1.0, 0.0, 0.0)


# ---------------------------------------------------------------- quaternion helpers


def qmul(a: Quat, b: Quat) -> Quat:
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    )


def qconj(q: Quat) -> Quat:
    return (-q[0], -q[1], -q[2], q[3])


def qnorm(q: Quat) -> Quat:
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    q = tuple(c / n for c in q)
    if q[3] < 0:  # one canonical sign, so snapshots don't flip
        q = tuple(-c for c in q)
    return tuple(0.0 if abs(c) < 1e-9 else c for c in q)


def qrot(q: Quat, v: Vec3) -> Vec3:
    x, y, z, _ = qmul(qmul(q, (v[0], v[1], v[2], 0.0)), qconj(q))
    return (x, y, z)


def euler_deg(x: float, y: float, z: float, order: str = "YXZ") -> Quat:
    """Euler angles in degrees -> quaternion. Godot's default order is YXZ, Unity's ZXY applied (= YXZ composed)."""
    qx = (math.sin(math.radians(x) / 2), 0.0, 0.0, math.cos(math.radians(x) / 2))
    qy = (0.0, math.sin(math.radians(y) / 2), 0.0, math.cos(math.radians(y) / 2))
    qz = (0.0, 0.0, math.sin(math.radians(z) / 2), math.cos(math.radians(z) / 2))
    parts = {"X": qx, "Y": qy, "Z": qz}
    q = IDENTITY
    for axis in order:
        q = qmul(q, parts[axis])
    return qnorm(q)


# ---------------------------------------------------------------- 3x3 basis <-> quaternion + scale (Godot Transform3D)


def basis_from(q: Quat, s: Vec3) -> list[list[float]]:
    """Rows of R * diag(s)."""
    x, y, z, w = q
    r = [
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ]
    return [[r[i][j] * s[j] for j in range(3)] for i in range(3)]


def decompose(rows: list[list[float]]) -> tuple[Quat, Vec3]:
    """Rows of a basis -> (rotation, scale). A mirrored basis puts the minus sign on scale.x."""
    cols = [[rows[i][j] for i in range(3)] for j in range(3)]
    s = [math.sqrt(sum(c * c for c in col)) for col in cols]
    det = (rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
           - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
           + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0]))
    if det < 0:
        s[0] = -s[0]
    m = [[rows[i][j] / (s[j] or 1.0) for j in range(3)] for i in range(3)]
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0:
        k = math.sqrt(tr + 1.0) * 2
        q = ((m[2][1] - m[1][2]) / k, (m[0][2] - m[2][0]) / k, (m[1][0] - m[0][1]) / k, 0.25 * k)
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        k = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2
        q = (0.25 * k, (m[0][1] + m[1][0]) / k, (m[0][2] + m[2][0]) / k, (m[2][1] - m[1][2]) / k)
    elif m[1][1] > m[2][2]:
        k = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2
        q = ((m[0][1] + m[1][0]) / k, 0.25 * k, (m[1][2] + m[2][1]) / k, (m[0][2] - m[2][0]) / k)
    else:
        k = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2
        q = ((m[0][2] + m[2][0]) / k, (m[1][2] + m[2][1]) / k, 0.25 * k, (m[1][0] - m[0][1]) / k)
    return qnorm(q), (s[0], s[1], s[2])


# ---------------------------------------------------------------- Unity <-> TwinScene


def mirror_pos(p: Vec3) -> Vec3:
    return (-p[0], p[1], p[2])


def mirror_rot(q: Quat) -> Quat:
    return (q[0], -q[1], -q[2], q[3])


def from_unity(pos: Vec3, rot: Quat, scale: Vec3, *, corrected: bool = False, parent_corrected: bool = False):
    """A Unity local transform -> TwinScene local transform.

    corrected: this object is a camera/directional/spot light (gets the half turn).
    parent_corrected: its parent got the half turn, so undo it here.
    """
    p, q = mirror_pos(pos), mirror_rot(rot)
    if parent_corrected:
        p, q = qrot(qconj(HALF_TURN_Y), p), qmul(qconj(HALF_TURN_Y), q)
    if corrected:
        q = qmul(q, HALF_TURN_Y)
    return clean(p), qnorm(q), tuple(scale)


def to_unity(pos: Vec3, rot: Quat, scale: Vec3, *, corrected: bool = False, parent_corrected: bool = False):
    """The inverse of from_unity."""
    p, q = tuple(pos), tuple(rot)
    if corrected:
        q = qmul(q, qconj(HALF_TURN_Y))
    if parent_corrected:
        p, q = qrot(HALF_TURN_Y, p), qmul(HALF_TURN_Y, q)
    return clean(mirror_pos(p)), qnorm(mirror_rot(q)), tuple(scale)


def clean(v: Vec3) -> Vec3:
    return tuple(0.0 if abs(c) < 1e-7 else c for c in v)
