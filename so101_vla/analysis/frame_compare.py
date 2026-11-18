"""학습과 현재 RGB 프레임 비교. 장치를 열지 않는다."""
CAMERAS=('end_effector','ceiling_vertical','ceiling_oblique')

def compare(training, current, destination, *, current_label='Live first observation'):
    from PIL import Image, ImageDraw

    canvas = Image.new('RGB',(1280, 3*510),'white')
    draw = ImageDraw.Draw(canvas)
    for i, role in enumerate(CAMERAS):
        for column, (root, prefix, label) in enumerate(((training,'training','Blue training'),
                                                        (current,'live',current_label))):
            with Image.open(root/f'{prefix}_{role}.png') as frame:
                if frame.size != (640,480) or frame.mode != 'RGB':
                    raise ValueError(f'{role} source resolution or RGB mismatch')
                canvas.paste(frame, (640*column,510*i+26))
            draw.text((640*column+12,510*i+7),f'{label} | camera{i+1} | {role}',fill='black')
    canvas.save(destination)
