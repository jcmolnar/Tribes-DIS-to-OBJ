# Tribes Interior → OBJ Exporter (`dis2obj`)

Convert **Starsiege Tribes / Darkstar (1998)** interior models — the buildings,
forts, towers and bases shipped inside `.vol` archives — into textured
**Wavefront `.obj`** files you can open in Blender, 3ds Max, Maya, Godot, etc.

It reads the original on-disk formats directly (no game install or engine build
required) and writes:

- `<name>.obj` — geometry (vertices, UVs, faces)
- `<name>.mtl` — materials, one per texture
- `<name>_textures/` — each texture decoded to a PNG (palette already applied)

Pure Python 3, standard library only. **No Pillow, no extra packages.**

---

## What a Tribes interior is

Inside a `.vol` archive an interior is split across a few files:

| ext   | what it is                                                            |
|-------|----------------------------------------------------------------------|
| `.dis`| tiny manifest (an *ITRShape*) naming the detail levels — *not* geometry |
| `.dig`| **the actual polygons** (an *ITRGeometry* BSP mesh), one per detail level |
| `.dil`| precomputed lighting (ignored by this tool)                          |
| `.dml`| material list — the texture filename for each material slot          |

The textures themselves (`.bmp`, 8-bit palettized) live in *other* `.vol`s
(e.g. `RPGtexturesh.vol`), and they don't carry their own palette — they
reference a shared world palette (`.ppl`). The exporter handles all of this for
you when you pass `--textures`.

---

## Requirements

- **Python 3.7+** (`python --version`)
- For `--textures`: the game's `.vol` files (textures + palettes are read from
  them). **Easiest: drop this `dis2obj` folder anywhere inside your Tribes
  folder** — next to `base/` and `RPG/`, or in a subfolder, or in the root. The
  tool walks up from its own location and finds the Tribes folder automatically.
  If you keep it elsewhere, point it at the game with `--vol-dir "path\to\Tribes"`.

Geometry export (no `--textures`) needs nothing but the input file.

---

## Quick start

1. Unzip this `dis2obj` folder **into your Tribes folder** (the one with
   `base/` and `RPG/` inside it).
2. Open a terminal in the `dis2obj` folder and convert an interior archive:

```
python dis2obj.py "..\RPG\ncity.vol" --textures
```

(Or give a full path to any `.vol`.) The Tribes folder is detected
automatically — no paths to configure.

This writes everything to a sibling folder `ncity_obj\` next to the `.vol`.
Open the `.obj` in Blender (**File ▸ Import ▸ Wavefront (.obj)**) and switch the
viewport to **Material Preview** (top-right sphere icons, or press **Z**) to see
the textures.

> Textures only show in *Material Preview* or *Rendered* shading — the default
> *Solid* mode draws everything white. That is the #1 "it imported untextured"
> gotcha; it is not a problem with the files.

---

## Usage — works on ANY `.vol`

The input can be a `.vol`, a loose `.dig`/`.dis`, or a folder.

```
python dis2obj.py <input> [options]
```

### 1. Straight from a `.vol` (easiest)

```
python dis2obj.py castle.vol --textures
python dis2obj.py castle.vol --textures -o C:\out\castle    # choose output dir
```

Every interior `.dig` inside the archive is converted. The matching `.dml`
inside the same `.vol` is used automatically for texture names.

### 2. Many `.vol`s at once

```
python dis2obj.py "C:\Dynamix\Tribes\RPG\*.vol" --textures
```

(Let your shell expand the glob, or pass the filenames explicitly.)

### 3. Already-extracted files

If you've unpacked a `.vol` to loose files:

```
python dis2obj.py ncity-00.dig --textures            # one detail level
python dis2obj.py "C:\maps\ncity" --textures         # a folder of .dig files
```

When converting loose `.dig` files, the tool looks for a sibling `<name>.dml`
for texture names; override with `--dml path\to\file.dml`.

---

## Options

| option | meaning |
|--------|---------|
| `--textures`      | decode each material's bitmap to PNG and link it via `map_Kd` |
| `--vol-dir DIR`   | Tribes folder to search for texture/palette `.vol`s (auto-detected from the tool's location if omitted) |
| `--ppl FILE`      | force a specific world palette (`.ppl`); otherwise all world palettes are merged and the right one is picked per texture |
| `--dml FILE`      | use this material list instead of the one in the `.vol`/sibling |
| `--flip-v`        | escape hatch that inverts the texture V coordinate. **Leave this OFF** — the default texture orientation is already correct for Tribes interiors. Only try it on the rare model whose textures come in vertically inverted. |
| `--legacy-uv`     | horizontally flip the U texture coordinate. The default is correct for base-game shapes; rotated mod interiors (e.g. Kronos/RPG buildings like ncity) need this — use it if a model's textures look **mirrored left-to-right**. |
| `-o OUT`          | output `.obj` (single `.dig` input) **or** output directory (`.vol` input) |

Without `--textures` you still get geometry + a `.mtl` whose material names are
the texture filenames, so you can hook up textures yourself later.

---

## Notes & limitations

- **Detail levels:** an interior usually has `-00` (high), `-01`, … (LODs).
  They're all exported; import only `-00` unless you specifically want a LOD.
- **Coordinates:** emitted unchanged. Tribes interiors and Blender are both
  Z-up, so model orientation is correct out of the box.
- **Texture mapping:** each wall panel maps a sub-rectangle of its bitmap
  (Tribes stores a per-surface texture offset/size), so narrow panels show a
  correct slice instead of the whole texture squeezed in. UVs are rotated to
  Blender's convention automatically — textures come out upright, no `--flip-v`
  needed.
- **Portal/“link” surfaces** (invisible cell boundaries) are skipped — you only
  get the visible walls/floors/props.
- **Transparency:** transparent textures (grates, foliage) currently export
  fully opaque.
- **Compressed `.vol` entries** (RLE/LZH) aren't decoded — the tool warns and
  skips those. Standard interior/texture entries are stored uncompressed.
- **Loose textures:** The tool automatically scans for loose `.bmp` files in the directory tree under `--vol-dir` (matching the game engine's priority of loose files taking precedence over `.vol` archives) in addition to scanning inside `.vol` files.
- If a texture can't be found under `--vol-dir` (either loose or in a VOL), that material is left without an image (geometry/UVs are unaffected); the tool prints which ones were missing.

---

## Files in this package

| file | role |
|------|------|
| `dis2obj.py` | the converter (run this) |
| `volread.py` | reads `PVOL` archives (`.vol`) |
| `textures.py`| decodes Tribes bitmaps + `.ppl` palettes, writes PNG |
| `obj2vol.py` | **experimental** reverse direction — OBJ → `.vol` (`.dis`/`.dig`/`.dml`) |

Keep the `.py` files together in the same folder.

---

## Reverse direction (experimental): `obj2vol.py`

```
python obj2vol.py model.obj [-o out.vol] [--name shapename]
```

Builds a Tribes interior `.vol` (containing `.dis` + `.dig` + `.dml`) from an OBJ.
Geometry, UVs and materials **round-trip cleanly back through `dis2obj.py`**.

```
python obj2vol.py model.obj [-o out.vol] [--name shapename]
```

`obj2vol.py` is included here for convenient **geometry round-tripping** — on its
own it writes an **empty BSP**, which is fine for re-importing through `dis2obj.py`
but won't render/collide a complex interior in the live engine (that needs a real
BSP tree).

👉 **For the full OBJ → in-game interior pipeline** — real BSP/PVS/lighting, walk-in
buildings, voxelizing, texturing — use the companion repo, which ships a **prebuilt**
`objbuild.js` (no engine build needed):
**[Tribes-OBJ-to-DIS-Converter](https://github.com/jcmolnar/Tribes-OBJ-to-DIS-Converter)**.
There you run `node objbuild.js model.obj model-00.dig` to get a real `.dig`, then
`python obj2vol.py model.obj --dig model-00.dig -o model.vol` to pack it.

---

## How it works (for the curious)

The formats were taken straight from the Darkstar engine source:

- `.dig` is a `PERS` persistent block wrapping `ITRGeometry`; the geometry is
  flat arrays — `point3List` (verts), `point2List` (UVs), `vertexList`
  (point+UV index pairs) and `surfaceList` (polygons). A face is just the run of
  `vertexList` entries a surface points at. (Validated byte-exact against a real
  file.)
- `.vol` is a `PVOL` archive: a directory of `{name, offset, size}` records at
  the tail, with each file's bytes stored at its offset.
- Texture `.bmp`s are chunked `PBMP` (8-bit indices + mipmaps) or Windows DIB;
  the 8-bit indices are expanded to RGB through a `.ppl` "PL98" world palette,
  selected per-texture by a `paletteIndex` stored in the bitmap.
