"""Duplicate cutouts can deadlock triangulators; never collapse distinct slots."""
import math
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from gerber import deduplicate_slots

class CutoutTests(unittest.TestCase):
    def test_reversed_shifted_and_resampled_boundary(self):
        a=[[0,0],[4,0],[4,2],[0,2],[0,0]]
        b=[[4,2],[4,1],[4,0],[2,0],[0,0],[0,2]]
        c=[[x+.0002,y-.0001] for x,y in b]
        result,removed=deduplicate_slots([a,b,c])
        self.assertEqual((len(result),removed),(1,2))
        self.assertEqual(result[0],a[:-1])

    def test_same_bounds_do_not_make_two_shapes_identical(self):
        square=[[0,0],[2,0],[2,2],[0,2]]
        diamond=[[1,0],[2,1],[1,2],[0,1]]
        result,removed=deduplicate_slots([square,diamond])
        self.assertEqual((len(result),removed),(2,0))

    def test_distinct_close_slots_remain(self):
        a=[[0,0],[4,0],[4,2],[0,2]]
        b=[[x+.01,y] for x,y in a]
        self.assertEqual(deduplicate_slots([a,b])[1],0)

    def test_circle_sampling_direction_does_not_matter(self):
        a=[[2+math.cos(i*math.tau/128),3+math.sin(i*math.tau/128)] for i in range(128)]
        b=[[2+math.cos(i*math.tau/256),3+math.sin(i*math.tau/256)] for i in range(256)]
        self.assertEqual(deduplicate_slots([a,list(reversed(b))])[1],1)

    def test_empty_and_malformed(self):
        self.assertEqual(deduplicate_slots([]),([],0))
        with self.assertRaisesRegex(ValueError,'不足三个'):
            deduplicate_slots([[[0,0],[1,1],[0,0]]])

if __name__=='__main__':unittest.main()
