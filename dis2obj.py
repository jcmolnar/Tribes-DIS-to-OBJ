#!/usr/bin/env python3
"""
dis2obj.py - Export Starsiege Tribes / Darkstar interior geometry to Wavefront OBJ.

A Tribes interior is a small ".dis" manifest (an ITRShape) that names per-detail
geometry files: "<name>-NN.dig" (ITRGeometry) and "<name>-NNN.dil" (lighting).
The real polygons live in the .dig files. This tool parses a .dig directly --
the on-disk layout is exactly what engine/Interior/code/itrgeometry.cpp
ITRGeometry::read() consumes -- and writes an .obj (+ .mtl) you can import in
Blender (File > Import > Wavefront (.obj)).

Texture names come from the sibling "<name>.dml" (TS::MaterialList) if present;
material index N in a surface maps to the Nth .bmp name in that list.

Usage:
    python dis2obj.py <file-or-dir> [more...] [-o OUT.obj] [--dml FILE] [--flip-v]
                      [--textures] [--vol-dir DIR] [--ppl FILE]

  - Pass a .dig            -> writes <stem>.obj next to it.
  - Pass a .dis or a dir   -> converts every *.dig found alongside it.
  - -o only applies when a single .dig is given.
  - --textures             -> also extract each material's bitmap to PNG (into a
                              "<stem>_textures/" folder) and wire map_Kd to them.
                              Finds the game VOLs + palettes automatically when the
                              tool sits inside your Tribes folder; else pass --vol-dir.

Coordinates are emitted unchanged (Tribes interiors and Blender are both Z-up).
"""

import sys
import os
import re
import glob
import struct
import argparse

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)


def find_tribes_root(*starts):
    """Locate the Tribes folder by walking up from each start dir. A Tribes root
    is recognised by a 'base/' subfolder containing .vol files; failing that, the
    nearest ancestor that itself holds .vol files. Returns a path or None."""
    chain = []
    seen = set()
    for start in starts:
        d = os.path.abspath(start)
        for _ in range(8):
            if d not in seen:
                seen.add(d); chain.append(d)
            p = os.path.dirname(d)
            if p == d:
                break
            d = p
    # canonical signature first: a base/ full of vols (palettes live there too)
    for c in chain:
        if glob.glob(os.path.join(c, "base", "*.vol")):
            return c
    # otherwise the nearest dir that holds vols directly or one level down
    for c in chain:
        if glob.glob(os.path.join(c, "*.vol")) or glob.glob(os.path.join(c, "*", "*.vol")):
            return c
    return None


# --- struct sizes, all confirmed byte-exact against ncity-00.dig (205164 bytes) ---
SZ_SURFACE      = 20   # see ITRGeometry::Surface (MSVC default packing)
SZ_BSPNODE      = 8
SZ_BSPLEAFSOLID = 12
SZ_BSPLEAFEMPTY = 44
SZ_VERTEX       = 4    # UInt16 pointIndex, UInt16 textureIndex
SZ_POINT3F      = 12   # 3 x float
SZ_POINT2F      = 8    # 2 x float
SZ_TPLANEF      = 16   # 4 x float

EXPECTED_VERSION = 7
SURFACE_TYPE_LINK = 1  # Surface::Type { Material = 0, Link = 1 }; bit 0 of byte 0


class Reader:
    def __init__(self, data):
        self.d = data
        self.p = 0

    def take(self, n):
        b = self.d[self.p:self.p + n]
        if len(b) != n:
            raise EOFError(f"unexpected end of file at offset {self.p} (wanted {n})")
        self.p += n
        return b

    def u16(self): return struct.unpack_from("<H", self.take(2))[0]
    def i32(self): return struct.unpack_from("<i", self.take(4))[0]
    def f32(self): return struct.unpack_from("<f", self.take(4))[0]


def parse_pers_header(r):
    """Consume the PERS block header + class name + version. Returns (classname, version)."""
    magic = r.take(4)
    if magic != b"PERS":
        raise ValueError(f"not a PERS block (magic={magic!r}) -- not a .dig geometry file?")
    _blocksize = r.i32()                      # bytes following this field
    namesize = r.u16()
    raw = r.take((namesize + 1) & ~1)         # name is padded to an even length
    name = raw[:namesize].decode("ascii", "replace")
    version = r.i32()
    return name, version


def parse_dig(path):
    """Parse one .dig file into geometry (see parse_dig_bytes)."""
    with open(path, "rb") as f:
        return parse_dig_bytes(f.read(), label=path)


def parse_dig_bytes(data, label="<bytes>"):
    """Parse .dig bytes into a dict with point3List, point2List, vertexList, surfaces."""
    r = Reader(data)

    cls, version = parse_pers_header(r)
    if cls != "ITRGeometry":
        raise ValueError(f"{label}: expected ITRGeometry, got {cls!r}")
    if version != EXPECTED_VERSION:
        print(f"  WARNING: {os.path.basename(label)} version {version}, "
              f"expected {EXPECTED_VERSION}; layout may differ.")

    r.i32()    # buildId
    r.f32()    # textureScale
    r.take(24) # box (Box3F)

    n_surface   = r.i32()
    n_node      = r.i32()
    n_solidleaf = r.i32()
    n_emptyleaf = r.i32()
    n_bit       = r.i32()
    n_vertex    = r.i32()
    n_point3    = r.i32()
    n_point2    = r.i32()
    n_plane     = r.i32()

    surface_blob = r.take(n_surface * SZ_SURFACE)
    r.take(n_node      * SZ_BSPNODE)
    r.take(n_solidleaf * SZ_BSPLEAFSOLID)
    r.take(n_emptyleaf * SZ_BSPLEAFEMPTY)
    r.take(n_bit       * 1)
    vertex_blob = r.take(n_vertex * SZ_VERTEX)
    point3_blob = r.take(n_point3 * SZ_POINT3F)
    point2_blob = r.take(n_point2 * SZ_POINT2F)
    r.take(n_plane * SZ_TPLANEF)

    r.i32()  # highestMipLevel
    r.i32()  # flags

    point3 = [struct.unpack_from("<fff", point3_blob, i * SZ_POINT3F)
              for i in range(n_point3)]
    point2 = [struct.unpack_from("<ff", point2_blob, i * SZ_POINT2F)
              for i in range(n_point2)]
    vertices = [struct.unpack_from("<HH", vertex_blob, i * SZ_VERTEX)
                for i in range(n_vertex)]  # (pointIndex, textureIndex)

    # Surface layout (offsets within the 20-byte record):
    #   0 bitfield, 1 material, 2-3 textureSize(x,y), 4-5 textureOffset(x,y),
    #   8-11 vertexIndex, 16 vertexCount.
    # The engine maps a surface's 0..1 point2List coords onto a sub-rectangle of
    # the bitmap: texel = textureOffset + point2*(textureSize+1)  (itrgeometry.cpp
    # getTextureCoord / itrrender.cpp registerTexture). Exporting raw point2List
    # stretches the FULL texture across every panel, squishing narrow ones; we
    # carry the rectangle so write_obj can reproduce the real per-panel mapping.
    surfaces = []
    for i in range(n_surface):
        off = i * SZ_SURFACE
        bits        = surface_blob[off]
        material    = surface_blob[off + 1]
        texSizeX    = surface_blob[off + 2] + 1
        texSizeY    = surface_blob[off + 3] + 1
        texOffX     = surface_blob[off + 4]
        texOffY     = surface_blob[off + 5]
        vertexIndex = struct.unpack_from("<I", surface_blob, off + 8)[0]
        vertexCount = surface_blob[off + 16]
        if (bits & 1) == SURFACE_TYPE_LINK:
            continue  # portal/link, not renderable geometry
        if vertexCount < 3:
            continue
        surfaces.append((material, vertexIndex, vertexCount,
                         texSizeX, texSizeY, texOffX, texOffY))

    return {
        "point3": point3,
        "point2": point2,
        "vertices": vertices,
        "surfaces": surfaces,
        "bytes_used": r.p,
        "total_bytes": len(r.d),
    }


# TS::MaterialList (.dml) layout, confirmed byte-exact against ncity.dml:
#   PERS header (8) + namesize(2) + "TS::MaterialList"(16) + version(4)
#   then MaterialList::read: int fnDetails, int fnMaterials,
#   then fnDetails*fnMaterials Material::Params records of 64 bytes each,
#   with the char fMapFile[32] name at offset 16 within each record.
DML_MAT_RECORD = 64
DML_NAME_OFFSET = 16
DML_NAME_LEN = 32


def load_material_names(dml_path):
    """Texture names from a TS::MaterialList .dml file, in material-index order."""
    if not dml_path or not os.path.isfile(dml_path):
        return None
    with open(dml_path, "rb") as f:
        return material_names_from_bytes(f.read())


def material_names_from_bytes(data):
    """Texture names from TS::MaterialList .dml bytes, in exact material-index order."""
    if not data or data[:4] != b"PERS":
        return None
    namesize = struct.unpack_from("<H", data, 8)[0]
    off = 10 + ((namesize + 1) & ~1)   # past class name
    off += 4                            # version
    fnDetails, fnMaterials = struct.unpack_from("<ii", data, off)
    off += 8
    names = []
    for m in range(fnDetails * fnMaterials):
        rec = off + m * DML_MAT_RECORD
        raw = data[rec + DML_NAME_OFFSET: rec + DML_NAME_OFFSET + DML_NAME_LEN]
        name = raw.split(b"\x00", 1)[0].decode("latin1")
        names.append(name)
    return names


def find_dml_for(dig_path):
    """ncity-00.dig -> ncity.dml in the same directory, if it exists."""
    d = os.path.dirname(os.path.abspath(dig_path))
    stem = os.path.basename(dig_path)
    base = re.sub(r"-\d+\.dig$", "", stem, flags=re.IGNORECASE)
    cand = os.path.join(d, base + ".dml")
    return cand if os.path.isfile(cand) else None


_BMP_INDEX_CACHE = {}
_PALETTE_CACHE = {}


def _build_bmp_index(vol_dir):
    """Map lower 'name.bmp' -> Vol object, scanning every VOL under vol_dir.
    Only each VOL's directory is read (seek-based), so this is cheap. Cached."""
    if vol_dir in _BMP_INDEX_CACHE:
        return _BMP_INDEX_CACHE[vol_dir]
    from volread import Vol
    index = {}
    vols = glob.glob(os.path.join(vol_dir, "**", "*.vol"), recursive=True)
    for vp in vols:
        try:
            v = Vol(vp)
        except Exception:
            continue
        for e in v.entries:
            n = e.name.lower()
            if n.endswith(".bmp") and n not in index:
                index[n] = v
    _BMP_INDEX_CACHE[vol_dir] = (index, len(vols))
    return index, len(vols)


def _load_palettes(vol_dir, ppl_override):
    """Merge every world day-palette's multipalettes into one
    {paletteIndex: [(r,g,b)]*256} dict (project-global indices are consistent
    across worlds, so merging maximizes coverage). Returns (tables, sources). Cached."""
    cache_key = (vol_dir, ppl_override)
    if cache_key in _PALETTE_CACHE:
        return _PALETTE_CACHE[cache_key]
    from volread import Vol
    from textures import parse_ppl
    tables = {}
    sources = []

    def merge(data, label):
        try:
            t = parse_ppl(data)
        except Exception as e:
            return
        for k, v in t.items():
            if k is not None and k not in tables:
                tables[k] = v
        if tables.get(None) is None and t.get(None):
            tables[None] = t[None]
        sources.append(label)

    if ppl_override and os.path.isfile(ppl_override):
        with open(ppl_override, "rb") as f:
            merge(f.read(), os.path.basename(ppl_override))

    # auto-discover world .ppl inside *World.vol archives
    for wv in glob.glob(os.path.join(vol_dir, "**", "*World.vol"), recursive=True):
        try:
            v = Vol(wv)
        except Exception:
            continue
        for n in v.names():
            if n.lower().endswith(".ppl"):
                merge(v.read(n), n)
    _PALETTE_CACHE[cache_key] = (tables, sources)
    return tables, sources


def resolve_textures(materials, out_dir, vol_dir, ppl_override):
    """Extract each material's bitmap to a PNG in out_dir. Returns
    {material_index: png_basename} for the ones successfully extracted."""
    from textures import parse_bitmap, expand_rgb, write_png

    cached = vol_dir in _BMP_INDEX_CACHE
    if not cached:
        print(f"  scanning VOLs under {vol_dir} ...")
    bmp_index, nvols = _build_bmp_index(vol_dir)
    palettes, psrc = _load_palettes(vol_dir, ppl_override)
    if not cached:
        print(f"    indexed {len(bmp_index)} bitmaps across {nvols} VOLs; "
              f"{len(palettes) - (1 if None in palettes else 0)} palette indices "
              f"from {len(psrc)} .ppl")

    os.makedirs(out_dir, exist_ok=True)
    result = {}
    dims = {}
    missing_tex, missing_pal = [], []
    for idx, name in enumerate(materials):
        if not name:
            continue
        vol = bmp_index.get(name.lower())
        if vol is None:
            missing_tex.append(name)
            continue
        try:
            bmp = parse_bitmap(vol.read(name))
        except Exception as e:
            missing_tex.append(f"{name} ({e})")
            continue
        # Match the engine's palette resolution: the OGL cache renders via
        # getMPCache(paletteIndex) (the WORLD multipalette), NOT the embedded
        # palette. So prioritize the world palette by paletteIndex; an MS-DIB can
        # carry a placeholder GRAYSCALE embedded palette alongside a real
        # paletteIndex (RPG-mod textures) — embedded-first would wrongly write
        # grayscale PNGs. Fall back to embedded only when paletteIndex is absent.
        pal = None
        if bmp["paletteIndex"] is not None:
            pal = palettes.get(bmp["paletteIndex"])
        if pal is None:
            pal = bmp["embedded_palette"]
        if pal is None:
            pal = palettes.get(None)
            if bmp["paletteIndex"] is not None:
                missing_pal.append(f"{name}#{bmp['paletteIndex']}")
        if pal is None:
            missing_tex.append(f"{name} (no palette)")
            continue
        w, h, rgb = expand_rgb(bmp, pal)
        dims[idx] = (w, h)        # needed to normalize the per-surface UV rect
        # The UV mapping (v = texel/H) assumes vertically-flipped PNGs, so flip
        # the rows on write. Bitmap is top-down; OBJ V origin is bottom.
        rowlen = w * 3
        rgb = b"".join(rgb[y * rowlen:(y + 1) * rowlen] for y in range(h - 1, -1, -1))
        png = os.path.splitext(name)[0] + ".png"
        write_png(os.path.join(out_dir, png), w, h, rgb)
        result[idx] = png

    print(f"    extracted {len(result)}/{len([m for m in materials if m])} textures")
    if missing_tex:
        print(f"    NOT found ({len(missing_tex)}): {', '.join(missing_tex[:8])}"
              + (" ..." if len(missing_tex) > 8 else ""))
    if missing_pal:
        print(f"    palette index missing, used fallback: {', '.join(missing_pal[:8])}"
              + (" ..." if len(missing_pal) > 8 else ""))
    return result, dims


def write_obj(geo, out_obj, materials, flip_v, tex_map=None, tex_subdir=None,
              tex_dims=None, legacy_uv=False):
    out_mtl = os.path.splitext(out_obj)[0] + ".mtl"
    used_mats = set(s[0] for s in geo["surfaces"])
    tex_dims = tex_dims or {}

    def mat_name(idx):
        if materials and idx < len(materials):
            # strip extension for a tidy material name
            return os.path.splitext(materials[idx])[0]
        if idx == 255:
            return "NoMaterial"
        return f"material_{idx}"

    # .mtl
    with open(out_mtl, "w") as m:
        m.write("# Generated by dis2obj.py\n")
        for idx in sorted(used_mats):
            m.write(f"\nnewmtl {mat_name(idx)}\n")
            m.write("Kd 0.8 0.8 0.8\n")
            if tex_map and idx in tex_map:
                rel = tex_map[idx]
                if tex_subdir:
                    rel = f"{tex_subdir}/{rel}"
                m.write(f"map_Kd {rel}\n")
            elif materials and idx < len(materials):
                m.write(f"map_Kd {materials[idx]}\n")

    # .obj — UVs are computed per surface: the engine maps each panel's 0..1
    # point2List coords onto a sub-rectangle of the bitmap,
    #   texel = textureOffset + point2 * (textureSize+1),  coord = texel / bitmapDim
    # so a narrow panel samples a thin slice (not the whole squished texture) and
    # an atlas texture's textureOffset picks the right sub-region.
    # Two orientation conventions, differing ONLY in U (V is the same for both):
    #   default   -> base-game shapes: u = texel/W           (e.g. bedrop)
    #   legacy_uv -> rotated mod interiors: u = 1 - texel/W   (e.g. Kronos/RPG, ncity)
    # both verified against the game. vt is emitted per face-corner (per-surface mapping).
    verts = geo["vertices"]
    pt2 = geo["point2"]
    with open(out_obj, "w") as o:
        o.write("# Generated by dis2obj.py from Tribes interior geometry\n")
        o.write(f"mtllib {os.path.basename(out_mtl)}\n")
        o.write(f"o {os.path.splitext(os.path.basename(out_obj))[0]}\n")

        for (x, y, z) in geo["point3"]:
            o.write(f"v {x:.6g} {y:.6g} {z:.6g}\n")

        # accumulate per-corner UVs and faces together
        vt_lines = []
        face_lines = []
        vt_n = 0
        for surf in sorted(geo["surfaces"], key=lambda s: s[0]):
            mat, vi, vc, tsx, tsy, tox, toy = surf
            tw, th = tex_dims.get(mat, (256, 256))
            face_lines.append(f"usemtl {mat_name(mat)}")
            parts = []
            for k in range(vc):
                pidx, tidx = verts[vi + k]
                pu, pv = pt2[tidx]
                # Both modes share the same V; only U differs. Rotated mod
                # interiors (e.g. Kronos/RPG buildings) flip U; base-game shapes
                # (e.g. bedrop) use it directly.
                if legacy_uv:
                    u = 1.0 - (tox + pu * tsx) / tw
                else:
                    u = (tox + pu * tsx) / tw
                v = (toy + pv * tsy) / th
                if flip_v:                            # escape hatch: invert V
                    v = 1.0 - v
                vt_n += 1
                vt_lines.append(f"vt {u:.6g} {v:.6g}")
                parts.append(f"{pidx + 1}/{vt_n}")  # OBJ is 1-based
            face_lines.append("f " + " ".join(parts))

        o.write("\n".join(vt_lines))
        o.write("\n")
        o.write("\n".join(face_lines))
        o.write("\n")

    return out_mtl


def emit(geo, materials, out_obj, flip_v, do_textures, vol_dir, ppl_override,
         legacy_uv=False):
    """Shared back-end: write OBJ/MTL (+ optional PNG textures) for parsed geometry."""
    if geo["bytes_used"] != geo["total_bytes"]:
        print(f"  WARNING: parsed {geo['bytes_used']} of {geo['total_bytes']} bytes "
              f"(layout mismatch?)")
    if materials:
        print(f"  materials: {len(materials)}")
    else:
        print("  materials: none (using numeric names)")

    tex_map = None
    tex_subdir = None
    tex_dims = None
    if do_textures and materials:
        tex_subdir = os.path.splitext(os.path.basename(out_obj))[0] + "_textures"
        out_tex = os.path.join(os.path.dirname(os.path.abspath(out_obj)), tex_subdir)
        tex_map, tex_dims = resolve_textures(materials, out_tex, vol_dir,
                                             ppl_override)

    out_mtl = write_obj(geo, out_obj, materials, flip_v, tex_map, tex_subdir,
                        tex_dims, legacy_uv)
    print(f"  -> {out_obj}")
    print(f"  -> {out_mtl}")
    if tex_map:
        print(f"  -> {tex_subdir}/  ({len(tex_map)} PNG textures)")
    print(f"     {len(geo['point3'])} verts, {len(geo['surfaces'])} faces")


def convert_one(dig_path, out_obj, dml_override, flip_v,
                do_textures=False, vol_dir=None, ppl_override=None, legacy_uv=False):
    print(f"Converting {dig_path}")
    geo = parse_dig(dig_path)
    dml = dml_override or find_dml_for(dig_path)
    materials = load_material_names(dml)
    if materials and dml:
        print(f"  (materials from {os.path.basename(dml)})")
    emit(geo, materials, out_obj, flip_v, do_textures, vol_dir, ppl_override, legacy_uv)


def convert_vol(vol_path, out_dir, dml_override, flip_v,
                do_textures=False, vol_dir=None, ppl_override=None, legacy_uv=False):
    """Convert every interior .dig stored inside a .vol, reading bytes directly."""
    from volread import Vol
    v = Vol(vol_path)
    digs = [e.name for e in v.entries if e.name.lower().endswith(".dig")]
    if not digs:
        print(f"{vol_path}: no .dig geometry inside.")
        return

    # material list: external override, else the .dml inside this vol
    dml_bytes = None
    if dml_override and os.path.isfile(dml_override):
        with open(dml_override, "rb") as f:
            dml_bytes = f.read()
    else:
        dmls = [e.name for e in v.entries if e.name.lower().endswith(".dml")]
        if dmls:
            dml_bytes = v.read(dmls[0])
    materials = material_names_from_bytes(dml_bytes) if dml_bytes else None

    os.makedirs(out_dir, exist_ok=True)
    for dig in digs:
        print(f"Converting {os.path.basename(vol_path)}:{dig}")
        out_obj = os.path.join(out_dir, os.path.splitext(dig)[0] + ".obj")
        geo = parse_dig_bytes(v.read(dig), label=dig)
        emit(geo, materials, out_obj, flip_v, do_textures, vol_dir, ppl_override, legacy_uv)


def collect_digs(targets):
    digs = []
    for t in targets:
        if os.path.isdir(t):
            digs += sorted(glob.glob(os.path.join(t, "*.dig")))
        elif t.lower().endswith(".dig"):
            digs.append(t)
        elif t.lower().endswith(".dis"):
            d = os.path.dirname(os.path.abspath(t))
            digs += sorted(glob.glob(os.path.join(d, "*.dig")))
        else:
            print(f"  skipping {t} (not a .dig/.dis/dir)")
    # de-dup, preserve order
    seen, out = set(), []
    for d in digs:
        ad = os.path.abspath(d)
        if ad not in seen:
            seen.add(ad)
            out.append(d)
    return out


def main():
    ap = argparse.ArgumentParser(description="Export Tribes interior geometry to OBJ.")
    ap.add_argument("targets", nargs="+",
                    help="a .vol archive, a .dig/.dis file, or a directory of them")
    ap.add_argument("-o", "--out",
                    help="output .obj (single .dig input) or output directory (.vol input)")
    ap.add_argument("--dml", help="material list .dml (defaults to the one in the .vol / sibling)")
    ap.add_argument("--flip-v", action="store_true",
                    help="flip the V texture coordinate (try this if UVs look upside down)")
    ap.add_argument("--textures", action="store_true",
                    help="extract material bitmaps to PNG and wire map_Kd to them")
    ap.add_argument("--vol-dir", default=None,
                    help="Tribes folder to search for textures/palettes "
                         "(auto-detected from the script location if omitted)")
    ap.add_argument("--ppl", help="world palette .ppl override (else auto-discovered)")
    ap.add_argument("--legacy-uv", action="store_true",
                    help="horizontally flip the U texture coordinate. Default suits "
                         "base-game shapes; rotated mod interiors (e.g. Kronos/RPG "
                         "buildings) need this -- use it if textures look mirrored "
                         "left-to-right.")
    args = ap.parse_args()

    # resolve the Tribes folder for texture/palette lookup
    if args.textures and not args.vol_dir:
        # search from the script's location, the inputs' locations, then cwd
        starts = [SCRIPT_DIR]
        for t in args.targets:
            starts.append(os.path.dirname(os.path.abspath(t)))
        starts.append(os.getcwd())
        args.vol_dir = find_tribes_root(*starts)
        if not args.vol_dir:
            print("ERROR: --textures needs the Tribes game files, but none were found.\n"
                  "       Put this tool inside your Tribes folder (next to base/ and RPG/),\n"
                  "       or pass --vol-dir \"path\\to\\Tribes\".")
            return 1
        print(f"  using Tribes folder: {args.vol_dir}")

    # .vol inputs are converted whole (every interior .dig inside)
    vols = [t for t in args.targets if t.lower().endswith(".vol")]
    for vol in vols:
        out_dir = args.out if args.out else os.path.splitext(os.path.abspath(vol))[0] + "_obj"
        try:
            convert_vol(vol, out_dir, args.dml, args.flip_v,
                        do_textures=args.textures, vol_dir=args.vol_dir,
                        ppl_override=args.ppl, legacy_uv=args.legacy_uv)
        except Exception as e:
            print(f"  ERROR: {e}")

    digs = collect_digs([t for t in args.targets if not t.lower().endswith(".vol")])
    if not digs and not vols:
        print("No .vol/.dig inputs found.")
        return 1
    if args.out and len(digs) > 1:
        print("-o can only be used with a single .dig input.")
        return 1

    for dig in digs:
        out = args.out if args.out else os.path.splitext(dig)[0] + ".obj"
        try:
            convert_one(dig, out, args.dml, args.flip_v,
                        do_textures=args.textures, vol_dir=args.vol_dir,
                        ppl_override=args.ppl, legacy_uv=args.legacy_uv)
        except Exception as e:
            print(f"  ERROR: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
