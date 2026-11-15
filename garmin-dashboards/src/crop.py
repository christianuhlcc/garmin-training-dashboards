"""Crop a screenshot for close inspection: python -m src.crop <png> <y0> <y1> [x0 x1] -> build/screenshots/crop.png"""
import sys
from PIL import Image

img = Image.open(sys.argv[1])
y0, y1 = int(sys.argv[2]), int(sys.argv[3])
x0, x1 = (int(sys.argv[4]), int(sys.argv[5])) if len(sys.argv) > 5 else (0, img.width)
img.crop((x0, y0, x1, min(y1, img.height))).save("build/screenshots/crop.png")
print(img.size)
