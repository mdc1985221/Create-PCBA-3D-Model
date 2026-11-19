"""Fabrication pin-one rules across rotations, sides and ambiguous artwork."""
import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

sys.path[:0]=[str(Path(__file__).resolve().parents[1]/'src')]
from pin1_support import detect, stroke_glyphs


class Pin1Tests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'输入资料').mkdir()
        (self.root/'导入检查.json').write_text(json.dumps(dict(layers=dict(top_silk='part.gto',bot_silk='part.gto'))),encoding='utf8')
        self.data=dict(coordinate_frame=dict(gerber_x0=12.5,gerber_y0=-3.2))
        self.result=dict(side='top',rotation=0,center=[10,20],width=8,depth=10,pitch=[.8,.8])

    def tearDown(self):self.temp.cleanup()

    def silk(self,marks=(),outline=True,clear=False):
        a=math.radians(self.result['rotation']);c,s=math.cos(a),math.sin(a)
        sign=1 if self.result['side']=='top' else -1
        def xy(q):
            x,y=q;y*=sign
            return f'X{round((22.5+c*x-s*y)*10000)}Y{round((16.8+s*x+c*y)*10000)}'
        lines=['%FSLAX24Y24*%','%MOMM*%','%ADD10C,0.12*%','D10*']
        if outline:
            points=[(-4,-5),(4,-5),(4,5),(-4,5),(-4,-5)]
            lines.append(xy(points[0])+'D02*')
            lines.extend(xy(q)+'D01*' for q in points[1:])
        for corner,kind in marks:
            sx=-1 if corner.endswith('l') else 1;sy=1 if corner.startswith('t') else -1
            if kind=='diagonal':
                points=[(4*sx,4.5*sy),(3.5*sx,5*sy)]
            elif kind=='triangle':
                points=[(3.5*sx,4.5*sy),(3.1*sx,4.5*sy),(3.5*sx,4.1*sy),(3.5*sx,4.5*sy)]
            else:
                points=[(3.5*sx+.2*math.cos(i*math.pi/16),4.5*sy+.2*math.sin(i*math.pi/16)) for i in range(33)]
            lines.append(xy(points[0])+'D02*');lines.extend(xy(q)+'D01*' for q in points[1:])
        if clear:lines.extend(['%LPC*%',xy((3.5,4.5))+'D03*'])
        lines.append('M02*');(self.root/'输入资料/part.gto').write_text('\n'.join(lines),encoding='ascii')

    def test_all_corners_rotations_and_both_sides(self):
        for side in ['top','bottom']:
            for angle in [0,90,180,270,37]:
                self.result.update(side=side,rotation=angle)
                for corner in ['tl','tr','bl','br']:
                    self.silk([(corner,'diagonal')])
                    actual=detect(self.root,self.result,self.data)
                    self.assertEqual(actual['corner'],corner,(side,angle,actual))
                    self.assertEqual(actual['basis'],'fabrication_silk_geometry')

    def test_dot_triangle_and_conflicting_evidence(self):
        for kind in ['circle','triangle']:
            self.silk([('bl',kind)])
            self.assertEqual(detect(self.root,self.result,self.data)['corner'],'bl')
        self.silk([('bl','circle'),('tr','diagonal')])
        self.assertEqual(detect(self.root,self.result,self.data)['corner'],'unknown')

    def test_missing_outline_negative_artwork_and_missing_layer(self):
        for outline,clear in [(False,False),(True,True)]:
            self.silk([('br','circle')],outline,clear)
            self.assertEqual(detect(self.root,self.result,self.data)['corner'],'unknown')
        (self.root/'输入资料/part.gto').unlink()
        self.assertEqual(detect(self.root,self.result,self.data)['corner'],'unknown')

    def test_unmarked_rectangle_is_not_pin1(self):
        self.silk()
        self.assertEqual(detect(self.root,self.result,self.data)['corner'],'unknown')

    def test_stroke_labels_rotate_without_guessing_bare_numbers(self):
        templates={
            'A':[((0,0),(.5,1)),((.5,1),(1,0)),((.21,.42),(.79,.42))],
            '1':[((.18,.75),(.5,1)),((.5,1),(.5,0)),((0,0),(1,0))],
        }
        for label,lines in templates.items():
            for angle in [0,90,180,270]:
                a=math.radians(angle);c,s=math.cos(a),math.sin(a)
                primitives=[('stroke',True,[(2+.6*x*c-.8*y*s,3+.6*x*s+.8*y*c) for x,y in line],.1) for line in lines]
                found=list(stroke_glyphs(primitives))
                self.assertEqual([g[2] for g in found],[label],(label,angle,found))
        for lines in [[((0,0),(0,1))],[((0,0),(.5,1)),((.5,1),(1,0))],
                      [((0,1),(1,1)),((1,1),(.3,0))]]:
            self.assertEqual(list(stroke_glyphs([('stroke',True,line,.1) for line in lines])),[])

    def test_power_label_is_not_a_pin_label(self):
        # A next to V is part of VAA, not an isolated corner label.
        lines=[((0,0),(.3,.8)),((.3,.8),(.6,0)),((.126,.336),(.474,.336)),
               ((-.7,.8),(-.4,0)),((-.4,0),(-.1,.8))]
        self.assertEqual(list(stroke_glyphs([('stroke',True,line,.1) for line in lines])),[])

    def test_corner_label_and_conflicting_mark(self):
        self.silk()
        path=self.root/'输入资料/part.gto'
        original=path.read_text(encoding='ascii')
        # A beside the lower-left outline corner; the crossbar is essential.
        glyph=[((-4.6,-5.3),(-4.3,-4.5)),((-4.3,-4.5),(-4,-5.3)),
               ((-4.474,-4.964),(-4.126,-4.964))]
        commands=[]
        for line in glyph:
            for i,(x,y) in enumerate(line):
                commands.append(f'X{round((22.5+x)*10000)}Y{round((16.8+y)*10000)}D0{2 if i==0 else 1}*')
        path.write_text(original.replace('M02*','\n'.join(commands)+'\nM02*'),encoding='ascii')
        self.assertEqual(detect(self.root,self.result,self.data)['corner'],'bl')
        self.silk([('tr','diagonal')])
        path.write_text(path.read_text(encoding='ascii').replace('M02*','\n'.join(commands)+'\nM02*'),encoding='ascii')
        self.assertEqual(detect(self.root,self.result,self.data)['corner'],'unknown')


if __name__=='__main__':unittest.main()
