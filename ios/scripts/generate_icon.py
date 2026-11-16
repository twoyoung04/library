from PIL import Image, ImageDraw
from pathlib import Path

size = 1024
im = Image.new('RGB', (size, size), '#F4EFE3')
d = ImageDraw.Draw(im)
# A restrained stack of three clothbound volumes.
d.rounded_rectangle((170, 175, 854, 844), radius=170, fill='#243A34')
d.rounded_rectangle((264, 299, 404, 741), radius=24, fill='#D6AC82')
d.rounded_rectangle((428, 249, 566, 741), radius=24, fill='#F1E5CA')
d.rounded_rectangle((590, 327, 728, 741), radius=24, fill='#B76B4C')
for x, top, color in [(289, 299, '#7D523B'), (453, 249, '#9F8D70'), (615, 327, '#713F32')]:
    d.rounded_rectangle((x, top + 34, x + 90, top + 43), radius=4, fill=color)
    d.rounded_rectangle((x, 683, x + 90, 692), radius=4, fill=color)
path = Path(__file__).resolve().parents[1] / 'MyLibrary/Assets.xcassets/AppIcon.appiconset/AppIcon.png'
im.save(path)
