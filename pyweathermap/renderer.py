"""
2x supersampling + layered RGBA compositing.

Layer order (bottom → top):
  1. Background  (solid fill + optional background image)
  2. Links       (sharp fat arrows, anti-aliased via supersampling)
  3. Node shadows (blurred dark shapes offset behind each node)
  4. Nodes       (filled shapes with top-highlight rim)
  5. Labels      (BW labels as rounded pills; node labels)
  6. HUD         (gradient legend, title, timestamp)

All geometry is computed in logical 1x coordinates; the scale factor S=2
is applied only at draw time. The final image is downscaled with LANCZOS
to produce smooth sub-pixel edges.
"""

import math
import io
from datetime import datetime
from typing import List, Tuple
from PIL import Image, ImageDraw, ImageFont, ImageFilter
from .models import WeatherMap, MapNode, MapLink, Color

# ──────────────────────────────────────────────────────────────────────────────
# Fonts  (loaded once at import time with 2× sizes baked in)
# ──────────────────────────────────────────────────────────────────────────────

# Loads TrueType font.
def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSans.ttf",
    ):
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()

# ──────────────────────────────────────────────────────────────────────────────
# Global Defaults
# ──────────────────────────────────────────────────────────────────────────────
_SS = 2                                 # Supersampling factor
_PARALLEL_SPACING = 12                  # spacing between parallel links factor
_F_LABEL = _load_font(12 * _SS)         # Node label font size
_F_BW    = _load_font(10 * _SS)         # Link label font size
_F_TITLE = _load_font(16 * _SS)         # Title label font size
_F_SMALL = _load_font( 9 * _SS)         # Minimum font size

# ──────────────────────────────────────────────────────────────────────────────
# Colour helpers
# ──────────────────────────────────────────────────────────────────────────────

# Computes brightness of color.
def _luminance(c: tuple) -> float:
    r, g, b = c[:3]
    return 0.299 * r + 0.587 * g + 0.114 * b

# Blends color towards white by a fraction.
def _lighten(c: tuple, t: float) -> tuple:
    r, g, b = c[:3]
    return (min(255, int(r + (255 - r) * t)),
            min(255, int(g + (255 - g) * t)),
            min(255, int(b + (255 - b) * t)))

# ──────────────────────────────────────────────────────────────────────────────
# Geometry helpers  (all in logical 1× coordinates)
# ──────────────────────────────────────────────────────────────────────────────

# Calculates Euclidean distance.
def _distance(p1: Tuple, p2: Tuple) -> float:
    return math.hypot(p2[0] - p1[0], p2[1] - p1[1])

# Calculates unit vector from p1 to p2.
def _unit_vec(p1: Tuple, p2: Tuple) -> Tuple[float, float]:
    d = _distance(p1, p2)
    return ((p2[0]-p1[0])/d, (p2[1]-p1[1])/d) if d > 1e-9 else (1.0, 0.0)

# Rotates unit vector 90 degrees (perpendicular).
def _perp(ux: float, uy: float) -> Tuple[float, float]:
    return (-uy, ux)

# Walks a path and returns a point at fraction t of the path length with unit direction.
def _point_along_path(path: List[Tuple], t: float) -> Tuple[Tuple, Tuple]:
    total = sum(_distance(path[i], path[i+1]) for i in range(len(path)-1))
    if total < 1e-9:
        return path[0], (1.0, 0.0)
    target = total * t
    accum = 0.0
    for i in range(len(path)-1):
        seg = _distance(path[i], path[i+1])
        if accum + seg >= target or i == len(path)-2:
            lt = max(0.0, min(1.0, (target - accum) / seg)) if seg > 1e-9 else 0.0
            x = path[i][0] + (path[i+1][0]-path[i][0]) * lt
            y = path[i][1] + (path[i+1][1]-path[i][1]) * lt
            return (x, y), _unit_vec(path[i], path[i+1])
        accum += seg
    return path[-1], _unit_vec(path[-2], path[-1])

# ──────────────────────────────────────────────────────────────────────────────
# Low-level drawing primitives  (coordinates in supersampled space)
# ──────────────────────────────────────────────────────────────────────────────

# Rounds point to nearest integer pixels.
def _ipt(p) -> Tuple[int, int]:
    return (int(round(p[0])), int(round(p[1])))

# Calculates start and end of arrow body and arrow head.
# Draws arrow body and head using correctly scaled link width.
def _draw_fat_arrow(draw, p_from, p_to, width, color, S=1):
    p_from = (p_from[0] * S, p_from[1] * S)
    p_to   = (p_to[0] * S, p_to[1] * S)
    width  = width * S

    dx, dy = p_to[0]-p_from[0], p_to[1]-p_from[1]
    length = math.hypot(dx, dy)
    if length < 2:
        return

    ux, uy = dx/length, dy/length
    px, py = _perp(ux, uy)
    hw = width / 2.0
    arrow_len = min(width * 1.8, length * 0.35)
    arrow_w   = min(width * 1.1, hw * 2.4)
    se = (p_to[0] - ux*arrow_len, p_to[1] - uy*arrow_len)

    shaft = [
        (p_from[0]+px*hw, p_from[1]+py*hw),
        (se[0]+px*hw,     se[1]+py*hw),
        (se[0]-px*hw,     se[1]-py*hw),
        (p_from[0]-px*hw, p_from[1]-py*hw),
    ]
    head = [
        (se[0]+px*arrow_w, se[1]+py*arrow_w),
        p_to,
        (se[0]-px*arrow_w, se[1]-py*arrow_w),
    ]
    draw.polygon([_ipt(v) for v in shaft], fill=color)
    draw.polygon([_ipt(v) for v in head],  fill=color)

# Formats bps value to readable length
def format_bandwidth(bps: float) -> str:
    if bps >= 1e12:
        return f"{bps/1e12:.1f}T"
    if bps >= 1e9:
        return f"{bps/1e9:.1f}G"
    if bps >= 1e6:
        return f"{bps/1e6:.1f}M"
    if bps >= 1e3:
        return f"{bps/1e3:.1f}K"
    return f"{bps:.0f}"

# ──────────────────────────────────────────────────────────────────────────────
# Main renderer class
# ──────────────────────────────────────────────────────────────────────────────

class MapRenderer:
    # Store WeatherMpa dn superscaling factor on init.
    def __init__(self, wmap: WeatherMap):
        self.wmap = wmap
        self._S = _SS

# ── Primary ──────────────────────────────────────────────────────────────

    # Renders WeatherMap to PIL Image layer by layer.
    # Layers are background, links, node shadows, nodes, labels, and HUD.
    # Downscales to target size.
    def render(self) -> Image.Image:
        S = self._S
        m = self.wmap
        sw, sh = m.width * S, m.height * S
        bg_rgba = (*m.bgcolor.as_tuple(), 255)

        self._apply_scales()
        self._expand_box_nodes_for_labels()
        self._parallel_offsets = self._compute_parallel_offsets()

    # ── Layer 1: Background ────────────────────────────────────────────
        canvas = Image.new("RGBA", (sw, sh), bg_rgba)

    # ── Layer 2: Links ─────────────────────────────────────────────────
        link_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._draw_links(ImageDraw.Draw(link_layer), S)
        canvas = Image.alpha_composite(canvas, link_layer)

        bw_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._draw_bw_labels_all(ImageDraw.Draw(bw_layer), S)
        canvas = Image.alpha_composite(canvas, bw_layer)

    # ── Layer 3: Node shadows ──────────────────────────────────────────
        shadow_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._draw_node_shadows(ImageDraw.Draw(shadow_layer), S)
        shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(radius=S * 6))
        canvas = Image.alpha_composite(canvas, shadow_layer)

    # ── Layer 4: Nodes ─────────────────────────────────────────────────
        node_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        self._draw_nodes(ImageDraw.Draw(node_layer), S)
        canvas = Image.alpha_composite(canvas, node_layer)

    # ── Layer 5 & 6: Labels + HUD ──────────────────────────────────────
        hud_layer = Image.new("RGBA", (sw, sh), (0, 0, 0, 0))
        hud_draw = ImageDraw.Draw(hud_layer)
        self._draw_node_labels_all(hud_draw, S)
        self._draw_gradient_legend(hud_draw, self.wmap.scale, 12*S, 12*S, "Traffic Load", S)
        self._draw_title(hud_draw, S)
        canvas = Image.alpha_composite(canvas, hud_layer)

    # ── Downscale ──────────────────────────────────────────────────────
        result = canvas.resize((m.width, m.height), Image.LANCZOS)
        return result.convert("RGB")

    # Encodes to in-memory byte buffer to save without intermediate file.
    def render_to_bytes(self, fmt: str = "PNG") -> bytes:
        buf = io.BytesIO()
        self.render().save(buf, format=fmt)
        return buf.getvalue()

    # Iterates through nodes to find those with a set infourl field.
    # Returns name, area, and infourl to be used by server to create link areas.
    def get_node_areas(self) -> list:
        self._expand_box_nodes_for_labels()
        areas = []
        for node in self.wmap.nodes.values():
            if not node.infourl:
                continue
            x1 = int(node.x - node.icon_width / 2)
            y1 = int(node.y - node.icon_height / 2)
            x2 = int(node.x + node.icon_width / 2)
            y2 = int(node.y + node.icon_height / 2)
            areas.append((node.name, x1, y1, x2, y2, node.infourl))
        return areas
    
    def get_link_areas(self) -> list:
        areas = []
        for link in self.wmap.links.values():
            if not link.out_box or not link.in_box:
                continue
            x1, y1, x2, y2 = link.out_box
            areas.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "iface_from": link.iface1, "iface_to": link.iface2, "bandwidth": format_bandwidth(link.bandwidth), "pct": round(link.out_bps/link.bandwidth*100, 1)})
            x1, y1, x2, y2 = link.in_box
            areas.append({"x1": x1, "y1": y1, "x2": x2, "y2": y2, "iface_from": link.iface2, "iface_to": link.iface1, "bandwidth": format_bandwidth(link.bandwidth), "pct": round(link.in_bps/link.bandwidth*100, 1)})
        
        return areas

# ── Data resolution ─────────────────────────────────────────────────────────

    # Gets Color from WeatherMap scale for percentage of bandwidth used for in/out traffic.
    # Does not get when bandwidth is not set.
    def _apply_scales(self):
        scale = self.wmap.scale
        for link in self.wmap.links.values():
            if link.bandwidth <= 0:
                continue
            link.in_color  = scale.color_for_percentage(link.in_bps  / link.bandwidth * 100)
            link.out_color = scale.color_for_percentage(link.out_bps / link.bandwidth * 100)

    # Finds all node pairs with more than one link between them.
    # Iterates through pairs, adding parallel offset to links.
    # Allows multiple links between nodes without overlapping links.
    def _compute_parallel_offsets(self) -> dict:
        groups = {}
        for link in self.wmap.links.values():
            key = frozenset((link.node1, link.node2))
            groups.setdefault(key, []).append(link)

        offsets = {}
        for key, links in groups.items():
            if len(links) == 1:
                offsets[links[0].name] = 0.0
                continue
            canonical_first, _ = sorted(key)
            links = sorted(links, key=lambda l: l.name)
            n = len(links)
            for i, link in enumerate(links):
                magnitude = (i - (n - 1) / 2) * _PARALLEL_SPACING
                offsets[link.name] = magnitude if link.node1 == canonical_first else -magnitude
        return offsets

    # Iterates through nodes, expanding those that do not fit label text.
    # Expands width enough to fit label at _F_SMALL, minimum font size.
    def _expand_box_nodes_for_labels(self):
        S = self._S
        dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
        for node in self.wmap.nodes.values():
            text = node.label or node.name
            bb = dummy.textbbox((0, 0), text, font=_F_SMALL)
            tw = bb[2] - bb[0]
            # available_w in _draw_node_label is icon_width * S - S*6; use S*8 for
            # 4 logical px of padding per side so the text doesn't touch the edge.
            min_w = math.ceil((tw + S * 8) / S)
            if min_w > node.icon_width:
                node.icon_width = min_w
                half_w = node.icon_width / 2
                half_h = node.icon_height / 2
                margin = 2  # logical px
                node.x = min(max(node.x, half_w + margin), self.wmap.width - half_w - margin)
                node.y = min(max(node.y, half_h + margin), self.wmap.height - half_h - margin)


# ── Link rendering ───────────────────────────────────────────────────────────

    # Computes start and end points of path from node locations.
    # Applies parallel offset in case of multiple links on same path.
    # Returns path and split point at midway point.
    def _link_geometry(self, link: MapLink):
        n1 = self.wmap.nodes.get(link.node1)
        n2 = self.wmap.nodes.get(link.node2)
        if not n1 or not n2:
            return None
        p1, p2 = (n1.x, n1.y), (n2.x, n2.y)

        offset = self._parallel_offsets.get(link.name, 0.0)
        if offset:
            px, py = _perp(*_unit_vec(p1, p2))
            p1 = (p1[0] + px * offset, p1[1] + py * offset)
            p2 = (p2[0] + px * offset, p2[1] + py * offset)

        full_path = [p1, p2]
        split_t   = 0.5
        split, _  = _point_along_path(full_path, split_t)
        return full_path, split

    # Iterates through links, computes path and split with _link_geometry.
    # Draws two arrows starting at each node and meeting at split point along path.
    def _draw_links(self, draw: ImageDraw.ImageDraw, S: int):
        for link in self.wmap.links.values():
            geo = self._link_geometry(link)
            if not geo:
                continue
            _, split = geo
            out_c = link.out_color.as_tuple() + (255,)
            in_c  = link.in_color.as_tuple()  + (255,)
            n1 = self.wmap.nodes[link.node1]
            n2 = self.wmap.nodes[link.node2]
            _draw_fat_arrow(draw, (n1.x, n1.y), split, link.width, out_c, S)
            _draw_fat_arrow(draw, (n2.x, n2.y), split, link.width, in_c,  S)

# ── Node rendering ───────────────────────────────────────────────────────────

    # Computes node center point (cx, cy), half-width w2, half-heigh h2.
    # Returns these and bounding box.
    def _node_box(self, node: MapNode, S: int):
        cx, cy = node.x * S, node.y * S
        w2, h2 = node.icon_width * S // 2, node.icon_height * S // 2
        return cx, cy, w2, h2, [cx-w2, cy-h2, cx+w2, cy+h2]

    # Iterates through nodes, drawing silhouette of each shape.
    def _draw_node_shadows(self, draw: ImageDraw.ImageDraw, S: int):
        dx, dy = S * 3, S * 4
        shadow = (0, 0, 0, 150)
        for node in self.wmap.nodes.values():
            _, _, w2, h2, _ = self._node_box(node, S)
            cx, cy = node.x * S + dx, node.y * S + dy
            box = [cx-w2, cy-h2, cx+w2, cy+h2]
            if node.icon_type == "rbox":
                radius = max(2, min(S * 10, w2 // 3))
                draw.rounded_rectangle(box, radius=radius, fill=shadow)
            else:
                draw.rectangle(box, fill=shadow)

    # Draws icon based on icon_type, and fill based on node_type.
    # Gives black outline and top highlight.
    def _draw_nodes(self, draw: ImageDraw.ImageDraw, S: int):
        for node in self.wmap.nodes.values():
            _, _, w2, h2, box = self._node_box(node, S)

            fill = (215, 238, 220) if node.node_type == "switch" else (215, 228, 250) if node.node_type == "endpoint/switch" else self.wmap.bgcolor.as_tuple()

            outline_c = Color(0, 0, 0).as_tuple()
            ol_w = max(1, S)

            if node.icon_type == "rbox":
                radius = max(2, min(S * 10, w2 // 3))
                draw.rounded_rectangle(box, radius=radius, fill=fill+(255,),
                                       outline=outline_c+(255,), width=ol_w)
                # Top-light rim (inner rounded rect, top portion)
                inner = [box[0]+S*2, box[1]+S*2, box[2]-S*2, box[1]+h2-S*2]
                if inner[2] > inner[0] and inner[3] > inner[1]:
                    draw.rounded_rectangle(inner, radius=max(1, radius-S*2),
                                           fill=_lighten(fill, 0.30)+(180,))

            else:
                draw.rectangle(box, fill=fill+(255,), outline=outline_c+(255,), width=ol_w)
                inner = [box[0]+S*2, box[1]+S*2, box[2]-S*2, box[1]+h2-S*2]
                if inner[2] > inner[0] and inner[3] > inner[1]:
                    draw.rectangle(inner, fill=_lighten(fill, 0.30)+(180,))

# ── BW labels ────────────────────────────────────────────────────────────────

    # Helper for _draw_bw_labels_all
    # Draws rounded pill box and text centered at (cx, cy).
    def _pill_label(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, text: str,
                    font, text_color, bg_color, border_color=None, S=1):
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2]-bbox[0], bbox[3]-bbox[1]
        pad_x, pad_y = S * 6, S * 3
        rx = cx - tw//2 - pad_x
        ry = cy - th//2 - pad_y
        rw = rx + tw + pad_x * 2
        rh = ry + th + pad_y * 2
        radius = (rh - ry) // 2
        draw.rounded_rectangle([rx, ry, rw, rh], radius=radius,
                                fill=bg_color,
                                outline=border_color or bg_color,
                                width=max(1, S))
        draw.text((cx - tw//2, cy - th//2), text, font=font, fill=text_color)
        return rx // S, ry // S, rw // S, rh // S # to save for HTML hover

    # Helper for _draw_bw_labels_all
    # Iterates through a safe range of the link.
    # Checks if this point is clear of any neighboring nodes or link text.
    # Returns safe location.
    def _safe_label_t(self, path, t_nominal, node1, node2, clearance, step,
                      avoid_points=None, label_spacing=30):
        avoid_points = avoid_points or []
        for t in [t_nominal + i * step for i in range(20)]:
            if not (0.05 < t < 0.95):
                break
            pt, _ = _point_along_path(path, t)
            clear = True
            for node in self.wmap.nodes.values():
                if node is node1 or node is node2:
                    continue
                d = _distance(pt, (node.x, node.y))
                half_diag = math.hypot(node.icon_width, node.icon_height) / 2
                if d < half_diag + clearance:
                    clear = False
                    break
            if clear:
                for ap in avoid_points:
                    if _distance(pt, ap) < label_spacing:
                        clear = False
                        break
            if clear:
                return t
        return t_nominal

    # Iterates through all links to draw all link labels.
    # Skips any with no bandwidth.
    # Uses geometry to find intial points (25% and 75%), uses _safe_label_t
    # to find safe final positions.
    # Places pill box and label for both in and out.
    def _draw_bw_labels_all(self, draw: ImageDraw.ImageDraw, S: int):
        placed_positions = []
        for link in self.wmap.links.values():
            if link.bandwidth <= 0:
                continue
            geo = self._link_geometry(link)
            if not geo:
                continue
            full_path, _ = geo

            def _fmt(bps):
                return format_bandwidth(bps)

            # Find safe out position
            n1 = self.wmap.nodes[link.node1]
            n2 = self.wmap.nodes[link.node2]
            out_t = self._safe_label_t(full_path, 0.25, n1, n2, 15, 0.02, placed_positions)
            out_pos, _ = _point_along_path(full_path, out_t)
            placed_positions.append(out_pos)
            # Find safe in position
            in_t  = self._safe_label_t(full_path, 0.75, n1, n2, 15, -0.02, placed_positions)
            in_pos,  _ = _point_along_path(full_path, in_t)
            placed_positions.append(in_pos)

            txt_c  = (20, 35, 70, 255)
            bg_c   = (255, 255, 255, 215)

            out_border = link.out_color.as_tuple() + (255,)
            in_border  = link.in_color.as_tuple()  + (255,)

            link.out_box = self._pill_label(draw,
                             int(out_pos[0]*S), int(out_pos[1]*S),
                             _fmt(link.out_bps), _F_BW, txt_c, bg_c, out_border, S)
            link.in_box = self._pill_label(draw,
                             int(in_pos[0]*S), int(in_pos[1]*S),
                             _fmt(link.in_bps), _F_BW, txt_c, bg_c, in_border, S)

# ── Node labels ──────────────────────────────────────────────────────────────

    # Iterates through nodes and calls _draw_node_label for each.
    def _draw_node_labels_all(self, draw: ImageDraw.ImageDraw, S: int):
        for node in self.wmap.nodes.values():
            self._draw_node_label(draw, node, S)

    # Gets the node center and half width from _node_box.
    # Determines the largest font possible to fit in the box, truncating if necessary.
    # Determines text color for link/no link.
    # Draws drop shadow for readability and then text.
    def _draw_node_label(self, draw, node: MapNode, S: int):
        cx, cy, w2, _, _ = self._node_box(node, S)
        text = node.label or node.name

        # pick the largest font that fits inside the node box.
        available_w = max(1, w2 * 2 - S * 6)
        font = _F_LABEL
        bb = draw.textbbox((0, 0), text, font=font)
        if bb[2] - bb[0] > available_w:
            font = _F_BW
            bb = draw.textbbox((0, 0), text, font=font)
        if bb[2] - bb[0] > available_w:
            font = _F_SMALL
            bb = draw.textbbox((0, 0), text, font=font)
        # Truncate with ellipsis if still too wide
        if bb[2] - bb[0] > available_w:
            while len(text) > 1:
                text = text[:-1]
                bb = draw.textbbox((0, 0), text + "…", font=font)
                if bb[2] - bb[0] <= available_w:
                    text += "…"
                    break
            bb = draw.textbbox((0, 0), text, font=font)

        tw, th = bb[2] - bb[0], bb[3] - bb[1]

        lx, ly = cx - tw // 2, cy - th // 2
        lx, ly = int(lx), int(ly)

        # Node text color is dependent on if link exists
        txt_c = (30, 100, 255, 255) if node.infourl else (20, 35, 70, 255)

        # 1-pixel shadow for readability, then text
        shadow_c = (0, 0, 0, 160) if _luminance(txt_c[:3]) > 128 else (255, 255, 255, 100)
        draw.text((lx + max(1, S//2), ly + max(1, S//2)), text, font=font, fill=shadow_c)
        draw.text((lx, ly), text, font=font, fill=txt_c)

# ── Legend/Title/Timestamp ───────────────────────────────────────────────────

    # Draws legend title, a rounded background, and gradient bar located at x,y.
    # Adds tick marks every 25%.
    # Uses WeatherMap MapScale for gradient bar colors.
    def _draw_gradient_legend(self, draw, scale, x, y, label, S):
        bar_w = 300 * S
        bar_h = 26 * S
        radius = bar_h // 2

        txt_c   = (20, 35, 70, 255)
        frame_c = (100, 120, 180, 255)
        bg_c    = (255, 255, 255, 200)

        # Title
        draw.text((int(x), int(y)), label, font=_F_SMALL, fill=txt_c)
        y += _F_SMALL.size + S * 4

        # Panel background
        panel = [x - S*4, y - S*4,
                 x + bar_w + S*4,
                 y + bar_h + S*16 + S*4]
        draw.rounded_rectangle(panel, radius=S*4, fill=bg_c, outline=frame_c, width=max(1,S))

        # Gradient bar
        for i in range(bar_w):
            pct = i / bar_w * 100
            c   = scale.color_for_percentage(pct).as_tuple() + (255,)
            draw.line([(int(x+i), int(y)), (int(x+i), int(y+bar_h))], fill=c)

        # Bar rounded outline on top
        draw.rounded_rectangle([x, y, x+bar_w, y+bar_h],
                                radius=radius, outline=frame_c, width=max(1, S))

        # Tick marks + labels
        for pct in (0, 25, 50, 75, 100):
            tx = int(x + pct/100 * bar_w)
            draw.line([(tx, int(y+bar_h)), (tx, int(y+bar_h+S*3))],
                      fill=frame_c, width=max(1, S))
            tick_text = f"{pct}%"
            tb = draw.textbbox((0,0), tick_text, font=_F_SMALL)
            tw = tb[2]-tb[0]
            draw.text((tx - tw//2, int(y+bar_h+S*4)), tick_text, font=_F_SMALL, fill=txt_c)

    # Collects configured title/timestamp colors, and calculates size.
    # Draws title text if set and timestamp in bottom-left.
    def _draw_title(self, draw: ImageDraw.ImageDraw, S: int):
        m = self.wmap
        txt_c = m.title_color.as_tuple() + (255,)
        ts_c  = m.time_color.as_tuple()  + (255,)

        bh = 28 * S  # bottom bar height
        bx = 8 * S

        if m.title:
            draw.text((bx, m.height*S - bh - S*2), m.title, font=_F_TITLE, fill=txt_c)
        ts = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
        draw.text((bx, m.height*S - S*14), ts, font=_F_SMALL, fill=ts_c)