"""Fixed bench scenarios; actor truth is never a detector input."""
import xml.etree.ElementTree as ET
import copy,math
NAMES=('walk-turn','screen-crossing','walls-follow')
MOTION_START=120
MOTION_END=160

def build_scene(text,name,walls_world=None):
 if name not in NAMES:raise ValueError(name)
 tree=ET.fromstring(text)
 tree.find(".//model[@name='garuda_x500']/link[@name='follow_camera']/pose").text='0.15 0 0.18 0 0.3 0'
 tree.find('.//actor/animation/interpolate_x').text='false'
 script=tree.find('.//actor/script');script.find('loop').text='true'
 trajectory=script.find('trajectory')
 for child in list(trajectory):trajectory.remove(child)
 for seconds,pose in [(0,'6 0 1 0 0 0'),(120,'6 0 1 0 0 0'),(140,'9 0 1 0 0 0'),(160,'9 3 1 0 0 0'),(600,'9 3 1 0 0 0')]:
  p=ET.SubElement(trajectory,'waypoint');ET.SubElement(p,'time').text=str(seconds);ET.SubElement(p,'pose').text=pose
 if name=='walls-follow':
  if walls_world is None:raise ValueError('walls-follow requires the PX4 walls.sdf path')
  source=ET.parse(walls_world).getroot().find('world');world=tree.find('world')
  expected={'box1':((5,0,7.5,0,0,0),(1,20,15)),'box2':((-3,5,7.5,0,0,0),(10,1,15)),'box3':((13,-10,7.5,0,0,0),(17,1,15)),'box4':((12,0,7.5,0,0,0),(1,20,15))}
  for model_name,(pose,size) in expected.items():
   model=source.find("model[@name='%s']"%model_name)
   if model is None or tuple(map(float,model.findtext('pose').split()))!=pose or any(tuple(map(float,v.text.split()))!=size for v in model.findall('.//box/size')) or len(model.findall('.//box/size'))!=2:
    raise ValueError('walls geometry does not match the clearance scorer: '+model_name)
  for child in list(world):
   if child.tag=='light' or (child.tag=='model' and child.get('name') not in ('garuda_x500','overview')) or child.tag=='scene':world.remove(child)
  world.append(copy.deepcopy(source.find('scene')))
  for child in source:
   if child.tag not in ('model','light'):continue
   model=copy.deepcopy(child)
   if model.tag=='model' and model.get('name')!='ground_plane':
    pose=[float(v) for v in model.findtext('pose').split()];x,y=pose[:2];pose[0]=7-y;pose[1]=x-8.5;pose[5]+=math.pi/2
    model.find('pose').text=' '.join(str(v) for v in pose)
   world.append(model)
  for point in trajectory.findall('waypoint'):
   if point.findtext('time') in ('160','600'):point.find('pose').text='9 1.5 1 0 0 0'
 if name=='screen-crossing':
  wall=tree.find(".//model[@name='occluder']");wall.find('pose').text='8 2 1.5 0 0 0'
  for size in wall.findall('.//box/size'):size.text='0.4 2 3'
 return ET.tostring(tree,encoding='unicode')
