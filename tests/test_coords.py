import math
import random
import unittest

from twinscene import coords
from twinscene.model import same, same_rotation


class Coords(unittest.TestCase):
    def test_marker_points_line_up(self):
        # What the two editors reported for the same .obj: Unity (-1,0,0),(0,2,0),(0,0,3); Godot (1,0,0),(0,2,0),(0,0,3).
        for unity, godot in [((-1, 0, 0), (1, 0, 0)), ((0, 2, 0), (0, 2, 0)), ((0, 0, 3), (0, 0, 3))]:
            p, _, _ = coords.from_unity(unity, coords.IDENTITY, (1, 1, 1))
            self.assertTrue(same(p, godot), (unity, p))

    def test_rotated_model_points_line_up(self):
        # A model turned in Unity must show the same mesh point in the same place in Godot.
        rnd = random.Random(1)
        for _ in range(50):
            q_u = coords.qnorm(tuple(rnd.uniform(-1, 1) for _ in range(4)))
            p_u = tuple(rnd.uniform(-5, 5) for _ in range(3))
            vert_file = tuple(rnd.uniform(-1, 1) for _ in range(3))
            vert_unity = coords.mirror_pos(vert_file)  # Unity's importer mirrors X
            world_unity = tuple(a + b for a, b in zip(coords.qrot(q_u, vert_unity), p_u))
            p_g, q_g, _ = coords.from_unity(p_u, q_u, (1, 1, 1))
            world_godot = tuple(a + b for a, b in zip(coords.qrot(q_g, vert_file), p_g))
            self.assertTrue(same(coords.mirror_pos(world_unity), world_godot, 1e-6))

    def test_camera_looks_the_same_way(self):
        # Unity camera at (0,1,-10) looking down +Z (toward the origin).
        p, q, _ = coords.from_unity((0, 1, -10), coords.IDENTITY, (1, 1, 1), corrected=True)
        self.assertTrue(same(p, (0, 1, -10)))
        forward_godot = coords.qrot(q, (0, 0, -1))  # Godot cameras look down -Z
        self.assertTrue(same(forward_godot, (0, 0, 1)), forward_godot)  # still toward the origin

    def test_child_of_camera_stays_put(self):
        rnd = random.Random(2)
        for _ in range(50):
            cam_p = tuple(rnd.uniform(-5, 5) for _ in range(3))
            cam_q = coords.qnorm(tuple(rnd.uniform(-1, 1) for _ in range(4)))
            kid_p = tuple(rnd.uniform(-2, 2) for _ in range(3))
            kid_q = coords.qnorm(tuple(rnd.uniform(-1, 1) for _ in range(4)))
            # world position of the child in Unity, mirrored into Godot space
            w_unity = tuple(a + b for a, b in zip(coords.qrot(cam_q, kid_p), cam_p))
            gp, gq, _ = coords.from_unity(cam_p, cam_q, (1, 1, 1), corrected=True)
            kp, kq, _ = coords.from_unity(kid_p, kid_q, (1, 1, 1), parent_corrected=True)
            w_godot = tuple(a + b for a, b in zip(coords.qrot(gq, kp), gp))
            self.assertTrue(same(coords.mirror_pos(w_unity), w_godot, 1e-6))
            # and its orientation in the world is the mirrored Unity one
            world_godot_rot = coords.qnorm(coords.qmul(gq, kq))
            world_unity_rot = coords.qnorm(coords.mirror_rot(coords.qmul(cam_q, kid_q)))
            self.assertTrue(same_rotation(world_godot_rot, world_unity_rot, 1e-6))

    def test_round_trip(self):
        rnd = random.Random(3)
        for flags in [(False, False), (True, False), (False, True), (True, True)]:
            for _ in range(30):
                p = tuple(rnd.uniform(-5, 5) for _ in range(3))
                q = coords.qnorm(tuple(rnd.uniform(-1, 1) for _ in range(4)))
                s = tuple(rnd.uniform(0.1, 3) for _ in range(3))
                g = coords.from_unity(p, q, s, corrected=flags[0], parent_corrected=flags[1])
                u = coords.to_unity(*g, corrected=flags[0], parent_corrected=flags[1])
                self.assertTrue(same(u[0], p, 1e-6) and same_rotation(u[1], q, 1e-6) and same(u[2], s, 1e-6))

    def test_basis_decompose_matches_godot(self):
        # Godot saved rotation_degrees (10, 20, 30), scale (1, 2, 3) as this basis (rows):
        rows = [[0.8434932, -0.8368241, 1.0104722], [0.49240386, 1.705737, -0.52094454],
                [-0.21461014, 0.62464905, 2.7762496]]
        q, s = coords.decompose(rows)
        self.assertTrue(same(s, (1, 2, 3)))
        self.assertTrue(same_rotation(q, coords.euler_deg(10, 20, 30, "YXZ")), q)
        back = coords.basis_from(q, s)
        self.assertTrue(same([c for r in back for c in r], [c for r in rows for c in r], 1e-5))

    def test_unity_sun_rotation(self):
        # Unity saved localEulerAngles (50, -30, 0) as this quaternion; Unity applies Z, then X, then Y.
        q_u = (0.40821794, -0.23456973, 0.10938166, 0.8754261)
        self.assertTrue(same_rotation(coords.euler_deg(50, -30, 0, "YXZ"), q_u), coords.euler_deg(50, -30, 0, "YXZ"))


if __name__ == "__main__":
    unittest.main()
