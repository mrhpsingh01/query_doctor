"""Assemble actual browser screenshots into a 60-second demo GIF. Requires Pillow."""
import argparse
import json
from pathlib import Path
from PIL import Image

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--frames',type=Path,default=Path('.test-artifacts/demo-frames'))
parser.add_argument('--output',type=Path,default=Path('docs/demo.gif'))
args=parser.parse_args()
manifest=json.loads((args.frames/'manifest.json').read_text())
frames=[]
for entry in manifest:
    with Image.open(args.frames/entry['file']) as original:
        frames.append(original.convert('RGB').resize((960,675),Image.Resampling.LANCZOS).quantize(colors=128))
args.output.parent.mkdir(parents=True,exist_ok=True)
frames[0].save(args.output,save_all=True,append_images=frames[1:],duration=[entry['duration'] for entry in manifest],loop=0,optimize=True,disposal=2)
with Image.open(args.output) as check:
    assert check.n_frames==len(manifest)
    duration=sum(check.seek(i) or check.info['duration'] for i in range(check.n_frames))
    assert duration==60000
print(f'{args.output}: {len(frames)} frames, {duration/1000:.0f}s, {args.output.stat().st_size} bytes')
