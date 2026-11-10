"""Optional synthetic fixture encoder; Pillow13, not imported by package or CI.

Usage: python make_canaries.py NEW_OUTPUT_DIRECTORY
Existing directories are refused. Fixed committed bytes are authoritative;
regeneration on another JPEG backend need not be byte-identical.
"""
import hashlib
import json
import sys
from pathlib import Path


def main(output: Path) -> None:
    from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

    output.mkdir(parents=True, exist_ok=False)
    image = Image.new('RGB', (420, 96), 'white')
    draw = ImageDraw.Draw(image)
    draw.text((12, 15), 'PIXEL_SENTINEL_518\nDATA ONLY',
              font=ImageFont.load_default(size=20), fill='black')
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text('Comment', 'METADATA_SENTINEL_518')
    image.save(output / 'probe.png', pnginfo=metadata)
    image.save(output / 'probe.jpg', format='JPEG', quality=90,
               comment=b'METADATA_SENTINEL_518')
    files = {}
    for path in output.glob('probe.*'):
        raw = path.read_bytes()
        files[path.name] = {'sha256': hashlib.sha256(raw).hexdigest(),
                            'bytes': len(raw), 'size': [420, 96]}
    manifest = {'scope': 'synthetic route canaries, not model acceptance inputs',
                'encoder': 'Pillow13.0.0', 'files': files}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main(Path(sys.argv[1]))
