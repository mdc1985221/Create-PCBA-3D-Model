"""CAD face boundaries must survive tessellation for correct viewer normals."""
from pathlib import Path
import sys, unittest, math
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

class SurfaceNormalsTests(unittest.TestCase):
    def test_box_faces_do_not_average_across_edges(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
        from user_models import mesh
        part=mesh(BRepPrimAPI_MakeBox(3,2,.75).Shape(),'body')
        vertices=[part['v'][i:i+3] for i in range(0,len(part['v']),3)]
        normals={}
        for start in range(0,len(part['i']),3):
            ids=part['i'][start:start+3];a,b,c=[vertices[i] for i in ids]
            u=[b[i]-a[i] for i in range(3)];v=[c[i]-a[i] for i in range(3)]
            n=[u[1]*v[2]-u[2]*v[1],u[2]*v[0]-u[0]*v[2],u[0]*v[1]-u[1]*v[0]]
            length=math.sqrt(sum(x*x for x in n));n=tuple(round(x/length,6) for x in n)
            for index in ids:normals.setdefault(index,set()).add(n)
        # A viewer computes a shared vertex's normal from adjoining triangles.
        # No box vertex may mix normals from perpendicular CAD faces.
        self.assertTrue(all(len(values)==1 for values in normals.values()))
        self.assertEqual(len({next(iter(values)) for values in normals.values()}),6)

    def test_closed_cylinder_face_still_shares_vertices(self):
        from OCP.BRepPrimAPI import BRepPrimAPI_MakeCylinder
        from user_models import mesh
        part=mesh(BRepPrimAPI_MakeCylinder(1,2).Shape(),'pin')
        usage={}
        for index in part['i']:usage[index]=usage.get(index,0)+1
        self.assertTrue(any(count>=4 for count in usage.values()))
        self.assertLess(len(part['v'])//3,len(part['i']))

if __name__=='__main__':unittest.main()
