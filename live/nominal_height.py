"""Nominal URDF height gate using only the standard library; no devices."""
import math
import xml.etree.ElementTree as ET


def matmul(a,b):return [[sum(a[i][k]*b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
def matvec(a,v):return [sum(row[k]*v[k] for k in range(3)) for row in a]
def identity():return [[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]]


def axis_rotation(axis,angle):
    length=math.sqrt(sum(v*v for v in axis))
    if not length:raise ValueError('URDF zero rotation axis')
    x,y,z=[v/length for v in axis];c=math.cos(angle);s=math.sin(angle);v=1-c
    return [[c+x*x*v,x*y*v-z*s,x*z*v+y*s],[y*x*v+z*s,c+y*y*v,y*z*v-x*s],[z*x*v-y*s,z*y*v+x*s,c+z*z*v]]


def height_reader(source,offset_mm):
    root=ET.parse(source).getroot();joints={j.find('child').get('link'):j for j in root.findall('joint')}
    chain=[];link='gripper'
    while link!='base':
        joint=joints[link];origin=joint.find('origin');axis=joint.find('axis')
        xyz=[float(x) for x in (origin.get('xyz','0 0 0') if origin is not None else '0 0 0').split()]
        rpy=[float(x) for x in (origin.get('rpy','0 0 0') if origin is not None else '0 0 0').split()]
        rotation=identity()
        for a,angle in zip(((0,0,1),(0,1,0),(1,0,0)),reversed(rpy)):
            rotation=matmul(rotation,axis_rotation(a,angle))
        direction=[float(x) for x in (axis.get('xyz','1 0 0') if axis is not None else '1 0 0').split()]
        chain.append((joint.get('name'),joint.get('type'),xyz,rotation,direction));link=joint.find('parent').get('link')
    chain.reverse();offset=[v/1000 for v in offset_mm]
    def height(values):
        angles=dict(zip(('Rotation','Pitch','Elbow','Wrist_Pitch','Wrist_Roll'),map(math.radians,values[:5])))
        position=[0.,0.,0.];rotation=identity()
        for name,kind,xyz,origin,axis in chain:
            translation=matvec(rotation,xyz);position=[a+b for a,b in zip(position,translation)]
            rotation=matmul(rotation,origin)
            if kind in ('revolute','continuous'):rotation=matmul(rotation,axis_rotation(axis,angles[name]))
            elif kind!='fixed':raise ValueError('unsupported nominal height joint: '+kind)
        return (position[2]+matvec(rotation,offset)[2])*1000
    return height
